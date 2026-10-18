import json,sys,urllib.request
d=json.load(open(sys.argv[1])); out=sys.argv[2]
url=d["result"]["result"]["image"]
data=urllib.request.urlopen(url,timeout=60).read()
open(out,"wb").write(data); print("saved",out,len(data))
