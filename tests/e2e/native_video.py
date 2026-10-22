"""A provider document fixture for native WebView codec/CSP/isolation checks.

The WebM is generated locally (160x90 alternating solid colors); no remote media
availability is implied. Browser tests exercise the actual Matrix preview UI.
"""
import base64
from pathlib import Path

PLAYER_URL = 'https://www.youtube-nocookie.com/embed/M7lc1UVf-VE?autoplay=0&playsinline=1&hl=ru'
clip = base64.b64encode(Path(__file__).with_name('fixtures').joinpath('preview.webm').read_bytes()).decode()
PROVIDER_HTML = '''<!doctype html><html><meta charset="utf-8"><title>Native video fixture</title>
<video muted playsinline preload="auto" src="data:video/webm;base64,''' + clip + '''"></video>
<script>
let isolated=false;try{parent.document.body;}catch{isolated=true;}
const video=document.querySelector('video');
video.addEventListener('loadeddata',()=>parent.postMessage({kind:'cinny-native-video-fixture',isolated,
width:video.videoWidth,height:video.videoHeight,ready:video.readyState},'*'),{once:true});
</script></html>'''

CHECK_PLAYER = '''src => new Promise((resolve,reject)=>{
 const frame=document.createElement('iframe');let timer;
 const cleanup=()=>{clearTimeout(timer);window.removeEventListener('message',receive);frame.remove()};
 const receive=event=>{
  if(event.source!==frame.contentWindow||event.origin!==new URL(src).origin||event.data?.kind!=='cinny-native-video-fixture')return;
  const result={...event.data,appOrigin:location.origin};cleanup();resolve(result);
 };
 frame.title='Native video capability check';frame.sandbox='allow-scripts allow-same-origin allow-presentation';
 frame.allow='encrypted-media; fullscreen; picture-in-picture';frame.referrerPolicy='strict-origin';
 frame.style.cssText='position:fixed;bottom:8px;left:8px;width:min(480px,calc(100vw - 16px));height:270px;z-index:9999';
 window.addEventListener('message',receive);timer=setTimeout(()=>{cleanup();reject(Error('Native video frame did not decode'))},20000);
 frame.src=src;document.body.append(frame);
})'''
