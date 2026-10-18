import json,base64,sys
d=json.load(open(sys.argv[1])); out=sys.argv[2]
def walk(o):
    if isinstance(o,dict):
        for v in o.values(): yield from walk(v)
    elif isinstance(o,list):
        for v in o: yield from walk(v)
    else: yield o
for v in walk(d):
    if isinstance(v,str) and len(v)>1000:
        b=base64.b64decode(v.split(",")[-1]); open(out,"wb").write(b); print("saved",out,len(b),b[:4]); break
else: print("NO IMAGE:",json.dumps(d)[:600])
