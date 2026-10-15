"""System-prompt reference generated from the very schemas sent to the provider."""
import json
from copy import deepcopy


def wire_tool_definitions(definitions):
    """Omit duplicate prose only when the provider has the matching system docs.

    Validation keywords and property names are untouched. In particular, a
    property called 'description' is an argument, not a schema annotation.
    """
    documented = tool_documentation(definitions)
    def schema(rule):
        if not isinstance(rule, dict):
            return deepcopy(rule)
        # Nested parameter annotations are not all rendered by _type. Keep any
        # prose absent from the generated reference rather than silently lose it.
        result = {k: deepcopy(v) for k, v in rule.items()
                  if not (k == 'description' and isinstance(v, str) and v in documented)}
        for key in ('properties', '$defs', 'definitions', 'patternProperties'):
            if key in result:
                result[key] = {name: schema(value) for name, value in result[key].items()}
        for key in ('items', 'additionalProperties', 'not', 'if', 'then', 'else'):
            if key in result:
                result[key] = schema(result[key])
        for key in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
            if key in result:
                result[key] = [schema(value) for value in result[key]]
        return result
    return [{**{k: deepcopy(v) for k, v in d.items() if k not in ('description', 'parameters')},
             'parameters': schema(d['parameters'])} for d in definitions]


def _type(rule):
    if 'anyOf' in rule:
        return ' | '.join(_type(option) for option in rule['anyOf'])
    if 'enum' in rule:
        return ' | '.join(json.dumps(v,ensure_ascii=False) for v in rule['enum'])
    if rule.get('type')=='object':
        return '{'+', '.join(f'{key}: {_type(value)}' for key,value in rule.get('properties',{}).items())+'}'
    if rule.get('type')=='array':return '['+_type(rule['items'])+']'
    kind=rule.get('type','value')
    label=' | '.join(kind) if isinstance(kind,list) else kind
    bounds=[]
    for key,symbol in (('minimum','>='),('maximum','<='),('exclusiveMinimum','>'),('exclusiveMaximum','<')):
        if key in rule:bounds.append(symbol+str(rule[key]))
    if 'maxLength' in rule:bounds.append('max '+str(rule['maxLength'])+' characters')
    return label+(' ('+', '.join(bounds)+')' if bounds else '')


def tool_documentation(definitions):
    lines=['## Tool reference (generated from the offered schemas)',
           'Use only the tools and argument values below. Include all required arguments; nullable fields may be null. '
           'Inspection returns observations; selection returns a pending action, not completed execution.']
    for definition in definitions:
        lines.extend(['', '### '+definition['name'], definition['description']])
        props=definition['parameters'].get('properties',{})
        if not props:lines.append('Arguments: none.')
        for name,rule in props.items():
            description=rule.get('description','')
            lines.append(f'- {name}: {_type(rule)}'+('. '+description if description else ''))
    return '\n'.join(lines)
