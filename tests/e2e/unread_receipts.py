"""Regression: real Synapse receipts, room/thread badges, polls and reloads."""
from e2e_common import *
from playwright.sync_api import expect
import re

run=uuid.uuid4().hex[:7]
users=['readalice'+run, 'readbob'+run]
for u in users:
    api('POST','/_matrix/client/v3/register',body={'username':u,'password':'pass123','auth':{'type':'m.login.dummy'}})
alice=login(users[0]); a=alice['access_token']; b=login(users[1])['access_token']
room=api('POST','/_matrix/client/v3/createRoom',b,{'name':'Unread regression '+run,'preset':'private_chat','invite':[alice['user_id']]})['room_id']
api('POST',f'/_matrix/client/v3/join/{room}',a,{})
old=send(b,room,text('old main message'))
root1=send(a,room,text('FOLLOWED THREAD ONE')); reply1=send(b,room,treply(root1,'first thread reply'))
root2=send(a,room,text('FOLLOWED THREAD TWO')); reply2=send(b,room,treply(root2,'second thread reply'))
poll_content={'org.matrix.msc1767.text':'POLL ROOT','org.matrix.msc3381.poll.start':{'question':{'org.matrix.msc1767.text':'POLL ROOT'},'kind':'org.matrix.msc3381.poll.disclosed','max_selections':1,'answers':[{'id':'a','org.matrix.msc1767.text':'Alpha'},{'id':'b','org.matrix.msc1767.text':'Beta'}]}}
poll=send(a,room,poll_content,'org.matrix.msc3381.poll.start')
poll_reply=send(b,room,treply(poll,'reply under poll'))
vote=send(b,room,{'m.relates_to':{'rel_type':'m.reference','event_id':poll},'org.matrix.msc3381.poll.response':{'answers':['a']}},'org.matrix.msc3381.poll.response')
main=send(b,room,text('unread main after threads'))
api('POST',f'/_matrix/client/v3/rooms/{room}/receipt/m.read/{old}',a,{'thread_id':'main'})
checks=[]
def check(name, ok, detail=''):
    checks.append(ok); print(('PASS ' if ok else 'FAIL ')+name+(' '+str(detail) if detail else ''),flush=True)

with sync_playwright() as p:
    page,errors=open_ui(p,alice)
    # Read the existing provider through React's development-independent fiber tree.
    # This is test-side introspection, no debug API is added to the application.
    page.evaluate('''() => {
      const element=document.getElementById('root');
      const key=Object.keys(element).find(k=>k.startsWith('__reactContainer'));
      const seen=new Set();
      function visit(f) {
        if(!f||seen.has(f)) return null; seen.add(f);
        const v=f.memoizedProps?.value;
        if(v && typeof v.getRoom==='function' && typeof v.sendReadReceipt==='function') return v;
        return visit(f.child)||visit(f.sibling);
      }
      window.testMx=visit(element[key]?.stateNode?.current)||visit(element[key]);
      if(!window.testMx) throw Error('Matrix provider not found');
    }''')
    def state():
        return page.evaluate('''id=>{const r=window.testMx.getRoom(id);return r&&{
          main:r.getRoomUnreadNotificationCount(),total:r.getUnreadNotificationCount(),
          threads:r.getThreads().map(t=>({id:t.id,count:r.getThreadUnreadNotificationCount(t.id),read:t.getReadReceiptForUserId(window.testMx.getUserId())?.eventId})),
        }}''',room)
    def count(tid): return next((t['count'] for t in state()['threads'] if t['id']==tid),-1)
    page.goto(APP+'/home/'+room+'/')
    page.wait_for_selector('text=unread main after threads',timeout=30000)
    page.wait_for_timeout(1800)
    # Reacquire provider after navigation/reload (window survives client routing only).
    page.evaluate('''() => {if(window.testMx)return; const e=document.getElementById('root'), k=Object.keys(e).find(k=>k.startsWith('__reactContainer')); const seen=new Set();function v(f){if(!f||seen.has(f))return;seen.add(f);const x=f.memoizedProps?.value;if(x?.getRoom&&x?.sendReadReceipt)return x;return v(f.child)||v(f.sibling)}window.testMx=v(e[k]?.stateNode?.current)||v(e[k]);}''')
    check('main auto-read at live bottom',state()['main']==0,state())
    check('main auto-read preserves unread threads', count(root1)>0 and count(root2)>0,state())
    nav=page.get_by_role('group',name='Активные треды')
    nav.get_by_role('button',name=re.compile('FOLLOWED THREAD ONE')).click()
    page.wait_for_selector('text=first thread reply',timeout=15000); page.wait_for_timeout(1800)
    check('opening thread clears its counter',count(root1)==0,state())
    check('reading one thread preserves another',count(root2)>0,state())
    check('read thread badge disappears',nav.get_by_role('button',name=re.compile('FOLLOWED THREAD ONE')).locator('[aria-label^="Unread messages"]').count()==0)
    check('read thread has no New Messages divider',page.get_by_text('New Messages',exact=True).count()==0)
    # Emulate delayed decrypted highlight counts after an already confirmed read.
    page.evaluate('''([rid,tid])=>{const r=window.testMx.getRoom(rid);r.setThreadUnreadNotificationCount(tid,'total',7);r.setThreadUnreadNotificationCount(tid,'highlight',2);}''',[room,root1])
    page.wait_for_timeout(400)
    check('confirmed receipt prevents stale encrypted counter from reviving badge',nav.get_by_role('button',name=re.compile('FOLLOWED THREAD ONE')).locator('[aria-label^="Unread messages"]').count()==0)
    nav.get_by_role('button',name=re.compile('POLL ROOT')).click()
    page.wait_for_selector('text=reply under poll',timeout=15000); page.wait_for_timeout(1800)
    check('poll thread clears its reply counter despite vote on root',count(poll)==0,state())
    check('poll thread receipt points at reply',next(t for t in state()['threads'] if t['id']==poll)['read']==poll_reply,state())
    page.keyboard.press('Escape'); page.wait_for_timeout(1800)
    check('explicit mark clears whole room',state()['total']==0,state())
    check('explicit mark clears every thread',all(t['count']==0 for t in state()['threads']),state())
    check('explicit mark removes divider',page.get_by_text('New Messages',exact=True).count()==0)
    # New replies must light up again after a successful mark.
    reply3=send(b,room,treply(root2,'new unread reply after mark'))
    page.wait_for_timeout(1800)
    check('new reply restores unread counter',count(root2)>0,state())
    check('new reply restores thread badge',nav.get_by_role('button',name=re.compile('FOLLOWED THREAD TWO')).locator('[aria-label^="Unread messages"]').count()==1)
    # An older receipt from another device must not blindly delete the room badge.
    api('POST',f'/_matrix/client/v3/rooms/{room}/receipt/m.read/{reply2}',a,{'thread_id':root2})
    page.wait_for_timeout(1200)
    check('older receipt leaves later reply unread',count(root2)>0,state())
    api('POST',f'/_matrix/client/v3/rooms/{room}/receipt/m.read.private/{reply3}',a,{'thread_id':root2})
    page.wait_for_timeout(1800)
    check('private receipt from another device clears thread',count(root2)==0,state())
    page.reload();page.wait_for_timeout(6500)
    page.screenshot(path=SHOTS+'/unread-after-reload.png')
    # UI should keep read badges cleared after rebuilding SDK models.
    nav=page.get_by_role('group',name='Активные треды')
    check('no stale thread badges or dots after reload',nav.locator('[aria-label^="Unread messages"]').count()==0)
    check('no JavaScript errors',not errors,errors)
print(f'{sum(checks)}/{len(checks)} passed',flush=True)
sys.exit(0 if all(checks) else 1)
