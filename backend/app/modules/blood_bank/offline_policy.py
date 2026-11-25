"""Deny-by-default catalogue of actual registered blood mutations.
This is a discovery contract, not an idempotency or no-effect authorization.
"""
def mutation_contracts(app):
    items=[]
    for rule in app.url_map.iter_rules():
        path=rule.rule
        if '/blood-bank/' not in path and '/clinical-orders/blood-products' not in path:continue
        for method in sorted(set(rule.methods or ())&{'POST','PUT','PATCH','DELETE'}):
            items.append({'operation_type':'blood_bank.'+rule.endpoint.rsplit('.',1)[-1], 'method':method,'path':path,'disposition':'online_authoritative_manual_reconcile_unknown_outcome','automatic_replay':False,'server_no_effect_disposition':False})
    return sorted(items,key=lambda x:(x['path'],x['method']))
