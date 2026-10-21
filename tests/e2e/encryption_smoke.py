"""Encrypted message and persisted-history regression with two disposable devices."""
import os,sys,uuid,time
from e2e_common import api,login,APP
from playwright.sync_api import sync_playwright
run=uuid.uuid4().hex[:8];users=['cryptoalice'+run,'cryptobob'+run]
for user in users:api('POST','/_matrix/client/v3/register',body={'username':user,'password':'pass123','auth':{'type':'m.login.dummy'}})
sessions=[login(u) for u in users]
room=api('POST','/_matrix/client/v3/createRoom',sessions[0]['access_token'],{'name':'Encrypted platform check','preset':'private_chat','invite':[sessions[1]['user_id']],'initial_state':[{'type':'m.room.encryption','state_key':'','content':{'algorithm':'m.megolm.v1.aes-sha2'}}]})['room_id']
api('POST','/_matrix/client/v3/join/'+room,sessions[1]['access_token'],{})
with sync_playwright() as p:
    browser=p.chromium.launch(args=['--no-proxy-server']);contexts=[];pages=[]
    for sess in sessions:
        context=browser.new_context(viewport={'width':1100,'height':760});contexts.append(context);page=context.new_page();pages.append(page)
        page.goto(APP+'/config.json');page.evaluate('s=>{localStorage.setItem("cinny_access_token",s.access_token);localStorage.setItem("cinny_device_id",s.device_id);localStorage.setItem("cinny_user_id",s.user_id);localStorage.setItem("cinny_hs_base_url","http://localhost:8008")}',sess)
        page.goto(APP+'/home/'+room+'/');page.locator('[data-editable-name="RoomInput"]').wait_for(timeout=40000);page.keyboard.press('Escape')
    pages[0].wait_for_timeout(4000)
    for index in [0,1]:
        message='ENCRYPTED ROUNDTRIP '+run+' '+str(index)
        editor=pages[index].locator('[data-editable-name="RoomInput"]');editor.fill(message);editor.press('Enter')
        pages[1-index].get_by_text(message,exact=True).last.wait_for(timeout=25000)
        print('PASS encrypted message decrypted on other device '+str(index),flush=True)
    events=api('GET','/_matrix/client/v3/rooms/'+room+'/messages?dir=b&limit=20',sessions[0]['access_token'])['chunk']
    encrypted=[e for e in events if e['type']=='m.room.encrypted'];assert len(encrypted)>=2
    assert not any(e['type']=='m.room.message' and 'ENCRYPTED ROUNDTRIP' in str(e.get('content')) for e in events)
    print('PASS server stores encrypted events without plaintext',flush=True)
    pages[1].reload();pages[1].get_by_text('ENCRYPTED ROUNDTRIP '+run+' 0',exact=True).last.wait_for(timeout=30000)
    print('PASS encrypted history survives reload',flush=True)
    browser.close()
