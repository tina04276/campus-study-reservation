const $ = (selector, root=document) => root.querySelector(selector);
const state = { token: localStorage.getItem('study-token') || '', user: null, selected: null, spaces: [] };

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}
function showMessage(target, message, error=false) {
  const box = typeof target === 'string' ? $(target) : target;
  box.textContent = message || '';
  box.classList.toggle('error', Boolean(error));
}
function taipeiDate(offset=0) {
  const parts = new Intl.DateTimeFormat('en-US', {timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
  const value = Object.fromEntries(parts.map(part => [part.type,part.value]));
  const day = new Date(Date.UTC(Number(value.year),Number(value.month)-1,Number(value.day)+offset));
  return day.toISOString().slice(0,10);
}
function taipeiToday(){return taipeiDate();}
function validateSlot(form){
  if(!form.date || !form.start_time || !form.end_time)return '請填寫日期、開始與結束時間。';
  if(form.end_time<=form.start_time)return '結束時間必須晚於開始時間。';
  if(new Date(form.date+'T'+form.start_time+':00+08:00')<=new Date())return '這個時段已經過了，請改選未來的日期或時間。';
  return '';
}
const errorMessages={
  'Reservation must start in the future':'預約時間已經過了，請改選未來的日期或時間。',
  'Seat is already reserved for this time':'這個座位剛被預約了，請重新查詢並選擇其他座位。',
  'Reservation is outside opening hours':'這個時段不在開放時間內，請重新選擇。',
  'Reservation must end after it starts on the same date':'結束時間必須晚於開始時間，且不可跨日。',
  'Invalid email or password':'Email 或密碼不正確。'
};
async function api(path, options={}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set('Authorization', 'Bearer ' + state.token);
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, {...options, headers});
  const data = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data && data.detail;
    const message = Array.isArray(detail) ? detail.map(x => x.msg).join('、') : (detail || '發生錯誤');
    throw new Error(errorMessages[message] || (response.status>=500?'系統暫時無法完成操作，請稍後重試。':message));
  }
  return data;
}
function logout() {
  state.token=''; state.user=null; state.selected=null;
  localStorage.removeItem('study-token');
  localStorage.removeItem('study-email');
  $('#app-view').classList.add('hidden'); $('#login-view').classList.remove('hidden');
  $('#session-label').textContent='請先登入';
}
function activatePanel(panelId, nav) {
  $$('.content-panel').forEach(el => el.classList.add('hidden'));
  $('#'+panelId).classList.remove('hidden');
  nav.querySelectorAll('.tab').forEach(button => button.classList.toggle('active', button.dataset.panel === panelId));
}
function $$(selector, root=document) { return Array.from(root.querySelectorAll(selector)); }
function setupSession(user) {
  state.user=user;
  $('#login-view').classList.add('hidden'); $('#app-view').classList.remove('hidden');
  $('#session-label').textContent=user.email+' · '+(user.role==='ADMIN'?'管理者':'學生');
  const isAdmin=user.role==='ADMIN';
  $('#student-nav').classList.toggle('hidden',isAdmin); $('#admin-nav').classList.toggle('hidden',!isAdmin);
  $('#page-title').textContent=isAdmin?'管理自習空間':'預約自習座位';
  activatePanel(isAdmin?'admin-spaces-panel':'booking-panel',isAdmin?$('#admin-nav'):$('#student-nav'));
  if (isAdmin) loadAdmin(); else { loadReservations(); searchSpaces(); }
}

$('#login-form').addEventListener('submit', async event => {
  event.preventDefault(); showMessage('#login-error','');
  const values=Object.fromEntries(new FormData(event.currentTarget));
  try {
    const result=await api('/api/login',{method:'POST',body:JSON.stringify(values)});
    state.token=result.access_token; localStorage.setItem('study-token',state.token); localStorage.setItem('study-email',result.user.email); setupSession(result.user);
  } catch (error) { showMessage('#login-error',error.message,true); }
});
$('#logout-button').addEventListener('click',logout);
$('#booking-date').min=taipeiToday();
$('#booking-date').value=taipeiDate(1);

$('#student-nav').addEventListener('click', event => {
  const button=event.target.closest('.tab'); if(!button)return;
  activatePanel(button.dataset.panel,$('#student-nav'));
  if(button.dataset.panel==='my-reservations-panel') loadReservations();
});
$('#admin-nav').addEventListener('click', event => {
  const button=event.target.closest('.tab'); if(!button)return;
  activatePanel(button.dataset.panel,$('#admin-nav'));
  if(button.dataset.panel==='admin-reservations-panel') loadAdminReservations();
});

$('#search-form').addEventListener('submit',event=>{event.preventDefault();searchSpaces();});
async function searchSpaces() {
  const form=Object.fromEntries(new FormData($('#search-form')));
  if(!form.date) form.date=taipeiToday();
  const validation=validateSlot(form);
  if(validation){state.selected=null;$('#selection-banner').classList.add('hidden');$('#spaces-list').replaceChildren();showMessage('#booking-message',validation,true);return;}
  const query=new URLSearchParams(form);
  $('#spaces-list').replaceChildren(node('p','muted','查詢中…'));
  $('#selection-banner').classList.add('hidden'); state.selected=null;
  try {
    const spaces=await api('/api/spaces?'+query.toString());
    state.spaces=spaces; renderSpaces(spaces); showMessage('#booking-message',spaces.length?'選一個可預約的座位，再確認預約資訊。':'目前沒有啟用中的自習空間。');
  } catch(error){$('#spaces-list').replaceChildren();showMessage('#booking-message',error.message,true);}
}
function renderSpaces(spaces) {
  const root=$('#spaces-list'); root.replaceChildren();
  for(const space of spaces){
    const card=node('article','space-card');
    const available=space.seats.filter(seat=>seat.is_available).length;
    card.append(node('h3','',space.name),node('p','space-meta',space.location+' · 可預約 '+available+' / '+space.seats.length+' 席'));
    if(!space.is_open) card.append(node('p','closed-note','此時段非開放時間'));
    const seats=node('div','seat-list');
    if(!space.seats.length) seats.append(node('span','muted','尚未設定座位'));
    for(const seat of space.seats){
      const button=node('button','seat-button'+(seat.is_available?'':' busy'),seat.seat_code);
      button.type='button';button.disabled=!seat.is_available;button.dataset.seatId=seat.seat_id;button.setAttribute('aria-pressed','false');button.setAttribute('aria-label',seat.seat_code+(seat.is_available?' 可預約':' 不可預約'));
      button.addEventListener('click',()=>selectSeat(space,seat)); seats.append(button);
    }
    card.append(seats);root.append(card);
  }
}
function selectSeat(space,seat) {
  const form=Object.fromEntries(new FormData($('#search-form')));
  $$('.seat-button').forEach(button=>{const selected=button.dataset.seatId===seat.seat_id;button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));});
  state.selected={seat_id:seat.seat_id,seat_code:seat.seat_code,space_name:space.name,...form};
  $('#selection-text').textContent='已選擇 '+space.name+' · '+seat.seat_code+' · '+form.date+' '+form.start_time+'–'+form.end_time;
  $('#selection-banner').classList.remove('hidden');
}
$('#search-form').addEventListener('input',()=>{
  state.selected=null;$('#selection-banner').classList.add('hidden');$('#spaces-list').replaceChildren();
  showMessage('#booking-message','時段已變更，請重新查詢空位。');
});
$('#confirm-booking').addEventListener('click',()=>{
  if(!state.selected)return;
  const s=state.selected, validation=validateSlot(s);
  if(validation){showMessage('#booking-message',validation,true);return;}
  const summary=$('#booking-summary');summary.replaceChildren();
  for(const [label,value] of [['自習空間',s.space_name],['座位',s.seat_code],['日期',s.date],['時間',s.start_time+'–'+s.end_time+'（台灣時間）']])summary.append(node('dt','',label),node('dd','',value));
  showMessage('#dialog-error','');$('#booking-dialog').showModal();
});
$('#close-booking-dialog').addEventListener('click',()=>$('#booking-dialog').close());
$('#submit-booking').addEventListener('click',async()=>{
  if(!state.selected)return;
  const s=state.selected, validation=validateSlot(s);
  if(validation){showMessage('#dialog-error',validation,true);return;}
  const submit=$('#submit-booking'), close=$('#close-booking-dialog');
  submit.disabled=true;close.disabled=true;submit.textContent='送出中…';
  try{
    await api('/api/reservations',{method:'POST',body:JSON.stringify({seat_id:s.seat_id,start_at:s.date+'T'+s.start_time+':00+08:00',end_at:s.date+'T'+s.end_time+':00+08:00'})});
    state.selected=null;$('#selection-banner').classList.add('hidden');$('#booking-dialog').close();
    activatePanel('my-reservations-panel',$('#student-nav'));
    showMessage('#reservation-feedback','預約成功！'+s.space_name+' · '+s.seat_code+' · '+s.date+' '+s.start_time+'–'+s.end_time);
    await loadReservations();
  }catch(error){showMessage('#dialog-error',error.message,true);}
  finally{submit.disabled=false;close.disabled=false;submit.textContent='送出預約';}
});

async function loadReservations(){
  try{renderReservations(await api('/api/me/reservations'),'#my-reservations-list',true);}
  catch(error){showMessage('#my-reservations-list',error.message,true);}
}
$('#refresh-reservations').addEventListener('click',loadReservations);
function formatDate(value){return new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));}
function renderReservations(items,target,allowCancel){
  const root=$(target);root.replaceChildren();
  if(!items.length){root.append(node('p','empty-state','目前沒有預約紀錄，請到「查詢與預約」選擇座位。'));return;}
  for(const item of items){
    const card=node('article','reservation-card');const left=node('div');
    left.append(node('h3', '', item.space_name+' · '+item.seat_code),node('p','',formatDate(item.start_at)+' – '+formatDate(item.end_at)));
    if(item.email) left.append(node('p','',item.email));
    left.append(node('span','status-pill'+(item.status==='CANCELLED'?' cancelled':''),item.status==='RESERVED'?'已預約':'已取消'));
    card.append(left);
    if(allowCancel&&item.status==='RESERVED'){
      const button=node('button','ghost','取消預約');button.addEventListener('click',async()=>{
        if(!window.confirm('確定取消這筆預約？'))return;
        try{await api('/api/reservations/'+item.reservation_id,{method:'DELETE'});await loadReservations();await searchSpaces();}
        catch(error){window.alert(error.message);}
      });card.append(button);
    }
    root.append(card);
  }
}

async function loadAdmin(){
  try{
    state.spaces=await api('/api/admin/spaces');
    renderAdminSpaces();populateSpaceSelects();showMessage('#admin-message','空間資料已載入。');
  }catch(error){showMessage('#admin-message',error.message,true);}
}
$('#refresh-admin').addEventListener('click',loadAdmin);
function populateSpaceSelects(){
  $$('.space-select').forEach(select=>{
    const old=select.value;select.replaceChildren();
    for(const space of state.spaces){const option=node('option','',space.name+'（'+(space.is_active?'啟用':'停用')+'）');option.value=space.space_id;select.append(option);}
    if(state.spaces.some(s=>s.space_id===old))select.value=old;
  });
}
function renderAdminSpaces(){
  const root=$('#admin-spaces-list');root.replaceChildren();
  if(!state.spaces.length){root.append(node('p','muted','尚未建立空間。'));return;}
  for(const space of state.spaces){
    const card=node('article','admin-space');const top=node('div','admin-space-top');const info=node('div');
    info.append(node('h3','',space.name),node('p','',space.location));
    const stateButton=node('button','ghost',space.is_active?'停用空間':'啟用空間');
    stateButton.addEventListener('click',async()=>patchSpace(space,{is_active:!space.is_active}));
    top.append(info,stateButton);card.append(top);
    const seatRow=node('div','admin-seat-row');
    for(const seat of space.seats){
      const chip=node('span','seat-chip');chip.append(document.createTextNode(seat.seat_code+' '));
      const toggle=node('button','',seat.is_active?'停用':'啟用');toggle.addEventListener('click',async()=>{
        try{await api('/api/admin/seats/'+seat.seat_id,{method:'PATCH',body:JSON.stringify({is_active:!seat.is_active})});await loadAdmin();}
        catch(error){showMessage('#admin-message',error.message,true);}
      });chip.append(toggle);seatRow.append(chip);
    }
    card.append(node('p','',space.seats.length+' 個座位 · '+space.opening_hours.length+' 筆每週時段'),seatRow);
    root.append(card);
  }
}
async function patchSpace(space,body){
  try{await api('/api/admin/spaces/'+space.space_id,{method:'PATCH',body:JSON.stringify(body)});await loadAdmin();}
  catch(error){showMessage('#admin-message',error.message,true);}
}
$('#add-space-form').addEventListener('submit',async event=>{
  event.preventDefault();const body=Object.fromEntries(new FormData(event.currentTarget));
  try{await api('/api/admin/spaces',{method:'POST',body:JSON.stringify(body)});event.currentTarget.reset();await loadAdmin();showMessage('#admin-message','自習空間已新增。');}
  catch(error){showMessage('#admin-message',error.message,true);}
});
$('#add-seat-form').addEventListener('submit',async event=>{
  event.preventDefault();const body=Object.fromEntries(new FormData(event.currentTarget));
  try{await api('/api/admin/spaces/'+body.space_id+'/seats',{method:'POST',body:JSON.stringify({seat_code:body.seat_code})});event.currentTarget.querySelector('[name="seat_code"]').value='';await loadAdmin();showMessage('#admin-message','座位已新增。');}
  catch(error){showMessage('#admin-message',error.message,true);}
});
$('#hours-form').addEventListener('submit',async event=>{
  event.preventDefault();const body=Object.fromEntries(new FormData(event.currentTarget));
  try{await api('/api/admin/spaces/'+body.space_id+'/hours/'+body.weekday,{method:'PUT',body:JSON.stringify({open_time:body.open_time,close_time:body.close_time})});await loadAdmin();showMessage('#admin-message','開放時段已儲存。');}
  catch(error){showMessage('#admin-message',error.message,true);}
});
async function loadAdminReservations(){
  try{renderReservations(await api('/api/admin/reservations'),'#admin-reservations-list',false);}
  catch(error){showMessage('#admin-reservations-list',error.message,true);}
}
$('#refresh-admin-reservations').addEventListener('click',loadAdminReservations);

if(state.token){
  api('/api/me').then(user=>setupSession(user)).catch(()=>logout());
}
