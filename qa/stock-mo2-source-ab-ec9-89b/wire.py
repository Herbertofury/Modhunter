"""Hash the complete owned catalog, omitting only precise clock paths."""
from copy import deepcopy
import hashlib
import json

def normalized(value):
    result=deepcopy(value)
    if not isinstance(result.pop("generatedAt",None),str):
        raise ValueError("Missing expected catalog clock")
    for record in result["sources"]:
        if not isinstance(record.pop("timestamp",None),str):
            raise ValueError("Missing expected source clock")
    return result

def sha(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=True,separators=(",",":")).encode()).hexdigest()

def catalog_digests(native, protected_body, generation):
    if type(generation) is not int or generation!=1 or len(protected_body)>32*1024*1024:
        raise ValueError("Expected exact first published generation and bounded API response")
    wire=json.loads(protected_body)
    if wire.get("ok") is not True or wire.get("generation")!=generation:
        raise ValueError("Protected response not from first exact ready generation")
    full=normalized(native)
    public=normalized(wire['catalog'])
    result={}
    expected = ("id","mo2InternalName","enabled","source","priority")
    for n,item in enumerate(full['items']):
        # Actual protected catalog may decorate existing source facts, but
        # every position and identity-only field must be contiguous and owned.
        wire_item = public['items'][n]
        for k in expected:
            if k not in item or k not in wire_item:
                raise ValueError("Projection lacks a required seeded field")
            if k!="source" and item[k]!=wire_item[k]:
                raise ValueError("Protected/native ordered catalog identity drift")
    if (len(full['items'])!=11207 or len(public['items'])!=11207
           or full['profileId']!=public['profileId'] or full['profileName']!=public['profileName']):
        raise ValueError("Missing complete ordered profile")
    result['native']=sha(full)
    result['protectedCatalog']=sha(public)
    result['profile']=sha({k:full[k] for k in ('catalogId','profileId','profileName','game','gameRuntime')})
    result['nativeSources']=sha([x['source'] for x in full['items']])
    result['protectedSources']=sha([x['source'] for x in public['items']])
    return result
