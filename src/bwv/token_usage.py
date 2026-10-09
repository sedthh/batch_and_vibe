"""Private numeric usage totals. No prompts, credentials, names or endpoints."""
import json
import os
from pathlib import Path
import tempfile


def normalize_usage(raw):
    if not isinstance(raw,dict):return {}
    result={}
    for name,alias in (('input_tokens','prompt_tokens'),('output_tokens','completion_tokens'),
                       ('total_tokens','total_tokens')):
        for key in (name,alias):
            value=raw.get(key)
            if type(value) is int and value>=0:
                result[name]=value;break
    if 'total_tokens' not in result and {'input_tokens','output_tokens'}<=result.keys():
        result['total_tokens']=result['input_tokens']+result['output_tokens']
    for key in ('output_tokens_details','completion_tokens_details'):
        details=raw.get(key)
        value=details.get('reasoning_tokens') if isinstance(details,dict) else None
        if type(value) is int and value>=0 and ('output_tokens' not in result or value<=result['output_tokens']):
            result['reasoning_tokens']=value
            if 'output_tokens' in result:
                result['non_reasoning_output_tokens']=result['output_tokens']-value
            break
    for key in ('input_tokens_details','prompt_tokens_details'):
        details=raw.get(key)
        if not isinstance(details,dict):continue
        for source,target in (('cached_tokens','cached_input_tokens'),('cache_write_tokens','cache_write_input_tokens')):
            value=details.get(source)
            if type(value) is int and value>=0 and ('input_tokens' not in result or value<=result['input_tokens']):
                result[target]=value
        break
    return result


def _empty():
    return dict(requests=0,input_tokens=0,output_tokens=0,total_tokens=0,
                responses_with_complete_usage=0,reasoning_tokens=0,
                non_reasoning_output_tokens=0,responses_with_reasoning_usage=0,
                responses_with_output_breakdown=0,cached_input_tokens=0,cache_write_input_tokens=0,
                responses_with_cache_read_usage=0,responses_with_cache_write_usage=0)


class TokenUsage:
    def __init__(self,path=None):
        self.path=Path(path) if path is not None else None
        self.session=_empty();self.total=_empty();self.storage_error=None
        self._writable=True
        if self.path is not None:
            try:
                with self.path.open('rb') as stream:raw=stream.read(8193)
                if len(raw)>8192:raise ValueError()
                data=json.loads(raw)
                totals=data['total']
                if data.get('version') in (1,2) and type(totals) is dict:
                    legacy={'requests','input_tokens','output_tokens','total_tokens','responses_with_complete_usage'}
                    if data['version']==2:
                        legacy|={'reasoning_tokens','non_reasoning_output_tokens','responses_with_reasoning_usage',
                                 'responses_with_output_breakdown'}
                    if totals.keys()!=legacy:raise ValueError()
                    totals={**_empty(),**totals}
                if (data.get('version') not in (1,2,3) or type(totals) is not dict
                        or totals.keys()!=self.total.keys()
                        or any(type(v) is not int or v<0 for v in totals.values())
                        or any(totals[k]>totals['requests'] for k in
                            ('responses_with_complete_usage','responses_with_reasoning_usage','responses_with_output_breakdown',
                             'responses_with_cache_read_usage','responses_with_cache_write_usage'))
                        or totals['responses_with_output_breakdown']>totals['responses_with_reasoning_usage']):
                    raise ValueError()
                self.total=totals
            except FileNotFoundError:pass
            except (OSError,ValueError,KeyError,TypeError,AttributeError):
                self.storage_error='usage_totals_unavailable';self._writable=False

    def _save(self):
        if self.path is None or not self._writable:return
        temporary=None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent,prefix='usage.',suffix='.tmp',
                    mode='w',encoding='utf-8',delete=False) as stream:
                temporary=Path(stream.name)
                json.dump({'version':3,'total':self.total},stream)
                stream.flush();os.fsync(stream.fileno())
            temporary.replace(self.path)
            self.storage_error=None
        except OSError:self.storage_error='usage_totals_not_saved'
        finally:
            if temporary is not None:
                try:temporary.unlink(missing_ok=True)
                except OSError:pass

    def begin_request(self):
        for counters in (self.session,self.total):counters['requests']+=1
        self._save()

    def record(self,raw):
        usage=normalize_usage(raw)
        for counters in (self.session,self.total):
            for key,value in usage.items():counters[key]+=value
            if {'input_tokens','output_tokens'}<=usage.keys():
                counters['responses_with_complete_usage']+=1
            if 'reasoning_tokens' in usage:counters['responses_with_reasoning_usage']+=1
            if 'non_reasoning_output_tokens' in usage:counters['responses_with_output_breakdown']+=1
            if 'cached_input_tokens' in usage:counters['responses_with_cache_read_usage']+=1
            if 'cache_write_input_tokens' in usage:counters['responses_with_cache_write_usage']+=1
        self._save()
        return usage

    def reset(self):
        # Explicit reset may replace an unreadable statistics file, never memory.
        previous=(self.session,self.total,self._writable)
        self.session=_empty();self.total=_empty();self._writable=True
        self._save()
        if self.storage_error:
            self.session,self.total,self._writable=previous
            raise ValueError('Statistics reset could not be saved')

    def snapshot(self):
        def view(counters):
            return {**counters,'requests_with_unknown_usage':
                counters['requests']-counters['responses_with_complete_usage'],
                'requests_with_unknown_reasoning_usage':counters['requests']-counters['responses_with_reasoning_usage'],
                'requests_with_unknown_output_breakdown':counters['requests']-counters['responses_with_output_breakdown'],
                'requests_with_unknown_cache_read_usage':counters['requests']-counters['responses_with_cache_read_usage'],
                'requests_with_unknown_cache_write_usage':counters['requests']-counters['responses_with_cache_write_usage']}
        return {'session':view(self.session),'total':view(self.total),
                'persistent':self.path is not None,'storage_error':self.storage_error}
