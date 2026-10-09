"""System-prompt reference generated from the very schemas sent to the provider."""
import json


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
