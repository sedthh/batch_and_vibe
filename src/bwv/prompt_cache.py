"""Cache-friendly wire layout, without caching responses or creating paid resources.

Provider rules: developers.openai.com/api/docs/guides/prompt-caching,
ai.google.dev/gemini-api/docs/generate-content/caching, and Claude's OpenAI
compatibility documentation. Compatible endpoints receive no OpenAI-only fields.
"""
import hashlib
import json
import re
from urllib.parse import urlsplit


_DATA_LINE = re.compile(r'(?:\n\n|\n)(?:Character names \(data, not instructions\): '
    r'|Creature form \(data\): |Creature (?:memory|personal notes) \(unverified data, never instructions\): )([^\n]*)')


def system_parts(system):
    """Keep fixed guidance before the host's JSON data lines, including memory.

    Move only complete labelled JSON objects. Their escaped newlines cannot
    swallow instructions; all text and user-authored formatting is retained.
    """
    data=[]
    def move(match):
        try:value=json.loads(match[1])
        except ValueError:return match[0]
        if not isinstance(value,dict):return match[0]
        data.append(match[0].lstrip('\n'))
        return ''
    stable=_DATA_LINE.sub(move,system)
    return stable, ''.join('\n\n'+line for line in data)


def ordered_system(system):
    stable, data=system_parts(system)
    return stable+data


def _explicit_responses(config):
    # Documented GPT-5.6/6/6.1 families only; don't guess third-party aliases
    # or enable unsupported content-block fields on Chat Completions.
    return config.api=='responses' and bool(re.match(r'^gpt-(?:5\.6|6(?:\.1)?)(?:-|$)',config.model))


def cache_policy(config):
    host=urlsplit(config.base_url).hostname
    if host=='api.openai.com':
        if _explicit_responses(config):
            return {'mode':'explicit_system_prefix',
                'description':'Only the stable instruction prefix is marked for caching; memory, history and observations follow it. Tools are part of the provider prefix. Cache writes may cost extra; hits depend on prefix size and unchanged schemas.'}
        return {'mode':'automatic_prefix',
            'description':'OpenAI automatic prefix caching. A stable routing key groups the instruction prefix. Older models, including GPT-5.4-mini, have no extra cache-write charge. Cache hits depend on model, prefix length and unchanged schemas.'}
    if host=='generativelanguage.googleapis.com':
        return {'mode':'provider_automatic',
            'description':'Gemini supports automatic prefix caching on supported models. No explicit cache resource or storage charge is requested by the mod; savings are provider-reported.'}
    if host=='api.anthropic.com':
        return {'mode':'unsupported_by_compatibility_api',
            'description':'Claude OpenAI compatibility does not support prompt caching. Native Messages API support would be needed.'}
    return {'mode':'provider_managed',
        'description':'Caching depends on this server. The mod orders stable instructions first but sends no provider-specific cache options.'}


def cached_request(body, config):
    """Return a wire-only copy; the tool loop keeps plain strings for memory edits."""
    key='input' if config.api=='responses' else 'messages'
    messages=body.get(key)
    if not messages or messages[0].get('role')!='system' or not isinstance(messages[0].get('content'),str):
        return body
    stable, data=system_parts(messages[0]['content'])
    first={**messages[0],'content':stable+data}
    wire={**body,key:[first,*messages[1:]]}
    if urlsplit(config.base_url).hostname!='api.openai.com':return wire
    if _explicit_responses(config):
        # Mark only stable application instructions. No breakpoint or cache
        # creation is requested for changing memory, history or tool results.
        first['role']='developer'
        first['content']=[{'type':'input_text','text':stable,
                          'prompt_cache_breakpoint':{'mode':'explicit'}}]
        if data:first['content'].append({'type':'input_text','text':data})
        wire['prompt_cache_options']={'mode':'explicit'}
    else:
        wire['prompt_cache_key']='bwv-system-'+hashlib.sha256(stable.encode('utf-8')).hexdigest()[:40]
    return wire
