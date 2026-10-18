import base64,json,os,sys,time,urllib.request
ACCT=open(os.path.expanduser("~/.config/cloudflare/account-id")).read().strip()
TOK=open(os.path.expanduser("~/.config/cloudflare/claude-api-token")).read().strip()
src="data:image/jpeg;base64,"+base64.b64encode(open("base_left.jpg","rb").read()).decode()
KEEP=("Keep everything else IDENTICAL: same camera angle, same size and position, same dome shape, "
      "same polished chrome base, same perfectly flat pure green (#00FF00) background with no shadow. Only change the light: ")
frames={
 "f2_centerleft":"the white-hot lamp and reflector now point slightly left of the viewer, so the brightest spot sits between the left edge and the center of the dome.",
 "f3_center":"the white-hot lamp and reflector now face directly toward the viewer, so the brightest white-hot spot is in the exact center of the dome with bright rays.",
 "f4_centerright":"the white-hot lamp and reflector now point slightly right of the viewer, so the brightest spot sits between the center and the right edge of the dome.",
 "f5_right":"the white-hot lamp and reflector now point to the RIGHT, so the right side of the dome blazes bright red-white while the left side is darker red.",
 "f6_back":"the lamp now points directly AWAY from the viewer, so no hot spot is visible; the whole dome glows a soft, deeper red from behind.",
}
for name,change in frames.items():
    if os.path.exists(name+".jpg"): print("skip",name); continue
    body={"model":"xai/grok-imagine-image-2.0","input":{"prompt":KEEP+change,"aspect_ratio":"1:1","response_format":"b64_json",
          "image":{"url":src,"type":"image_url"}}}
    req=urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{ACCT}/ai/run",
        data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+TOK,"cf-aig-gateway-id":"default","Content-Type":"application/json"})
    t=time.time()
    try:
        d=json.loads(urllib.request.urlopen(req,timeout=180).read())
    except urllib.error.HTTPError as e:
        print(name,"HTTP",e.code,e.read()[:400].decode(errors="replace")); sys.exit(1)
    url=d.get("result",{}).get("result",{}).get("image")
    if not url: print(name,"NO IMAGE",json.dumps(d)[:400]); sys.exit(1)
    open(name+".jpg","wb").write(urllib.request.urlopen(url,timeout=60).read())
    print("saved",name,f"{time.time()-t:.0f}s")
