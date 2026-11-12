/* Q-House — konsol STAF (lewat voyage-gate). Tab mengikuti peran akun (me.peran): sorter, loket, supervisor; Barcode
   untuk supervisor. Data dari /api/state (polling 4 detik); setiap perubahan lewat /api/aksi (server memeriksa peran). */
(function(){
'use strict';
const LAY = {instant:{label:'SPX Instant', short:'Instant', cls:'p-instant'}, sameday:{label:'SPX Sameday', short:'Sameday', cls:'p-sameday'}};
const ST = {menunggu:['Menunggu antrian','p-wait'], disiapkan:['Sedang disiapkan','p-prep'], siap:['Barang siap diambil','p-ready'],
            selesai:['Selesai','p-done'], bermasalah:['Bermasalah','p-bad'], dibatalkan:['Dibatalkan','p-cancel']};
const BK = {menunggu:['belum dipindai','p-wait'], dipindai:['dipindai','p-done'], tidak_ditemukan:['tidak ditemukan','p-bad'], dikeluarkan:['dikeluarkan','p-cancel']};
const TABS = [['sorter','package-search','Sorter','sorter'],['loket','store','Loket','loket'],['supervisor','layout-dashboard','Supervisor','supervisor'],['barcode','qr-code','Barcode','supervisor']];
let S = null, ok = false;
const ui = {tab:null, sorterTab:'antrian', sel:null, bsel:null, err:'', loketNo:Number(ls('qhouse.loket'))||1, pick:null, lerr:'', q:''};

function ls(k,v){ try{ if(v===undefined) return localStorage.getItem(k); localStorage.setItem(k,v); }catch(e){ return null; } }
const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hhmm = ms => { if(!ms) return '–'; const d=new Date(ms); return String(d.getHours()).padStart(2,'0')+'.'+String(d.getMinutes()).padStart(2,'0'); };
const mins = ms => Math.max(0, Math.round(ms/60000));
const now = () => S ? S.now + (Date.now() - S._at) : Date.now();
const last4 = hp => String(hp||'').replace(/\D/g,'').slice(-4) || '––––';
const maskHp = hp => { const d=String(hp||'').replace(/\D/g,''); return d.length<6? d : d.slice(0,4)+'••••'+d.slice(-2); };
const layPill = l => `<span class="pill ${LAY[l]?.cls||''}">${esc(LAY[l]?.label||l)}</span>`;
const stPill = s => `<span class="pill ${ST[s]?.[1]||''}">${esc(ST[s]?.[0]||s)}</span>`;
const bkPill = s => `<span class="pill ${BK[s]?.[1]||''}">${esc(BK[s]?.[0]||s)}</span>`;
const boleh = p => S && S.me.peran.includes(p);
const all = () => S ? S.tickets : [];
const queue = () => all().filter(t => t.status==='menunggu'||t.status==='disiapkan').sort((a,b)=>a.created_at-b.created_at);
const byId = id => all().find(t => t.id===id);
const batasMs = () => (Number(S.config.batasMenit)||30)*60000;
const empty = (img, b, s) => `<div class="empty"><img src="/static/assets/${img}" alt="" width="96"><b>${b}</b>${s?`<span>${s}</span>`:''}</div>`;
function toast(msg){ const el=document.getElementById('toast'); el.textContent=msg; el.hidden=false; clearTimeout(toast._t); toast._t=setTimeout(()=>{el.hidden=true;},3200); }

async function muat(){
  try{ const r = await fetch('/api/state', {cache:'no-store'}); if(!r.ok) throw new Error(r.status); S = await r.json(); S._at = Date.now(); ok = true; }
  catch(e){ ok = false; }
}
async function aksi(body, pesan){
  try{
    const r = await fetch('/api/aksi', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const j = await r.json().catch(()=>({}));
    if(!r.ok){ toast(j.error || 'Perubahan belum tersimpan. Coba lagi.'); await muat(); render(); return false; }
    if(pesan || j.pesan) toast(pesan || j.pesan);
    await muat(); render(); return true;
  }catch(e){ toast('Koneksi terputus. Coba lagi.'); return false; }
}

/* ---------- shell ---------- */
function render(){
  const view = document.getElementById('view');
  const dot = document.getElementById('modedot'), tx = document.getElementById('modetext');
  dot.className = 'dot ' + (ok ? 'live' : 'warn');
  tx.textContent = ok ? `${S.me.nama} · ${S.me.peran.length ? S.me.peran.join(', ') : 'lihat saja'} · diperbarui otomatis` : 'Belum tersambung ke server. Mencoba lagi…';
  if(!S){ view.innerHTML = ''; return; }
  const tabs = TABS.filter(t => boleh(t[3]));
  if(!ui.tab || !tabs.some(t=>t[0]===ui.tab)){ const h=location.pathname.replace('/',''); ui.tab = (tabs.find(t=>t[0]===h)||tabs[0]||[null])[0]; }
  document.getElementById('roles').innerHTML = tabs.map(t => `<a href="/${t[0]}" data-tab="${t[0]}" ${t[0]===ui.tab?'aria-current="page"':''}><i data-lucide="${t[1]}"></i><span>${t[2]}</span></a>`).join('');
  const keep = {}; view.querySelectorAll('input[id]').forEach(el => keep[el.id] = el.value);
  const act = document.activeElement && document.activeElement.id;
  view.innerHTML = !ui.tab ? `<div class="card panel">${empty('vidi-sleepy.png','Akunmu belum punya peran di Q-House','Minta kepala divisi memberi peran Sorter, Loket, atau Supervisor lewat Voyage › Manage › Akses App.')}</div>`
    : ({sorter:vSorter, loket:vLoket, supervisor:vSupervisor, barcode:vBarcode}[ui.tab])();
  Object.keys(keep).forEach(id => { const el = document.getElementById(id); if(el && !el.dataset.fresh) el.value = keep[id]; });
  if(act){ const el = document.getElementById(act); if(el) el.focus({preventScroll:true}); }
  if(window.lucide) window.lucide.createIcons({attrs:{'stroke-width':2}});
}

/* ---------- SORTER ---------- */
function vSorter(){
  const list = ui.sorterTab==='bermasalah' ? all().filter(t=>t.status==='bermasalah') : queue();
  if(!ui.sel || !list.find(t=>t.id===ui.sel)) ui.sel = list[0] ? list[0].id : null;
  const t = ui.sel && byId(ui.sel);
  const nQ = queue().length, nB = all().filter(t=>t.status==='bermasalah').length;
  return `<div class="console">
    <section class="card panel" aria-label="Daftar tiket">
      <div class="tabs" role="tablist">
        <button role="tab" aria-selected="${ui.sorterTab==='antrian'}" data-act="s-tab" data-v="antrian">Antrian <span class="count num">${nQ}</span></button>
        <button role="tab" aria-selected="${ui.sorterTab==='bermasalah'}" data-act="s-tab" data-v="bermasalah">Bermasalah <span class="count num">${nB}</span></button>
      </div>
      <p class="hint">${ui.sorterTab==='antrian'?'Satu antrian untuk Instant dan Sameday, urut waktu tiket dibuat.':'Tiket yang tertahan menunggu keputusan supervisor.'}</p>
      ${list.length ? `<div class="qlist">${list.map(x=>{ const n=x.bookings.length, d=x.bookings.filter(b=>b.status==='dipindai').length;
          return `<button class="qitem" type="button" data-act="s-sel" data-id="${x.id}" aria-current="${x.id===ui.sel}">
            <span class="l1"><span class="no num">${esc(x.no)}</span>${x.status==='menunggu'?layPill(x.layanan):stPill(x.status)}</span>
            <span class="l2">${esc(x.nama)} · ${n} booking${x.status==='disiapkan'?` · ${d}/${n} dipindai${x.sorter?' · '+esc(x.sorter):''}`:` · menunggu ${mins(now()-x.created_at)} menit`}</span></button>`; }).join('')}</div>`
        : empty('vidi-sleepy.png', ui.sorterTab==='antrian'?'Antrian kosong':'Tidak ada tiket bermasalah', ui.sorterTab==='antrian'?'Tiket baru muncul di sini begitu driver menekan Kirim.':'Semua tiket berjalan normal.')}
    </section>
    <section class="card detail" aria-label="Detail tiket">${t ? sorterDetail(t) : empty('vidi-sleepy.png','Pilih tiket dari antrian','Detail nomor booking dan tempat memindai resi muncul di sini.')}</section>
  </div>`;
}
function sorterDetail(t){
  const bks = t.bookings, working = t.status==='disiapkan';
  if(working && (ui.bsel==null || !bks[ui.bsel] || bks[ui.bsel].status!=='menunggu')){ const i = bks.findIndex(b=>b.status==='menunggu'); ui.bsel = i>=0 ? i : null; }
  const canReady = working && bks.every(b=>b.status==='dipindai'||b.status==='dikeluarkan') && bks.some(b=>b.status==='dipindai');
  const sel = ui.bsel!=null ? bks[ui.bsel] : null;
  return `<div class="dhead">
      <div class="stack" style="gap:6px"><p class="eyebrow">Tiket</p><div class="no num">${esc(t.no)}</div>
        <div class="meta">${esc(t.nama)} · ${esc(maskHp(t.hp))}${t.plat?' · '+esc(t.plat):''} · dibuat ${hhmm(t.created_at)}</div></div>
      <div class="stack" style="gap:8px;align-items:flex-end">${stPill(t.status)}
        <span class="meta">${layPill(t.layanan)}${t.status!=='siap'?`<button class="btn btn-quiet btn-sm" type="button" data-act="s-lay"><i data-lucide="repeat"></i>Ubah ke ${LAY[t.layanan==='instant'?'sameday':'instant'].short}</button>`:''}</span></div>
    </div>
    ${t.status==='menunggu' ? `<div class="alert a-info"><i data-lucide="hand"></i><span>Ambil tiket ini agar driver melihat status <b>sedang disiapkan</b> dan sorter lain tidak mengerjakannya.</span></div>
      <button class="btn btn-primary" type="button" data-act="s-take"><i data-lucide="package-open"></i>Ambil tiket ${esc(t.no)}</button>` : ''}
    ${t.status==='bermasalah' ? `<div class="alert a-bad"><i data-lucide="alert-triangle"></i><span>Tiket tertahan. Supervisor memutuskan: cari ulang, keluarkan pesanan, atau batalkan tiket.</span></div>` : ''}
    ${working ? `<div class="scanbox">
        <label for="s-resi" class="eyebrow">${sel?`Pindai label untuk booking ${esc(sel.kode)}`:'Semua booking sudah diproses'}</label>
        <div class="scan"><input class="input num" id="s-resi" inputmode="numeric" autocomplete="off" placeholder="Pindai barcode resi atau ketik" ${sel?'':'disabled'}><button class="btn btn-primary" type="button" data-act="s-scan" ${sel?'':'disabled'}><i data-lucide="scan-line"></i>Simpan resi</button></div>
        <span class="hint">Cari paket yang labelnya mencetak kode booking ${sel?`<b>${esc(sel.kode)}</b>`:''}, lalu pindai barcode resinya. Pilih baris lain untuk mengganti booking.</span>
        ${ui.err?`<p class="err">${esc(ui.err)}</p>`:''}</div>` : ''}
    <div class="blist">${bks.map((b,i)=>`<div class="brow ${working && i===ui.bsel?'sel':''}">
        ${working && b.status==='menunggu' ? `<button class="selbtn" type="button" data-act="s-bsel" data-i="${i}" aria-label="Pilih booking ${esc(b.kode)}"></button>` : `<span style="width:22px"></span>`}
        <div style="min-width:0;display:flex;gap:12px;align-items:center;flex-wrap:wrap"><span class="k">${esc(b.kode)}</span><span class="rs">${b.resi?`resi <b>${esc(b.resi)}</b>`:'resi belum dipindai'}</span></div>
        <div class="acts">${bkPill(b.status)}${working && b.status==='menunggu'?`<button class="btn btn-danger btn-sm" type="button" data-act="s-nf" data-i="${i}">Tidak ditemukan</button>`:''}${working && b.status==='dipindai'?`<button class="btn btn-quiet btn-sm" type="button" data-act="s-undo" data-i="${i}">Ulangi</button>`:''}</div>
      </div>`).join('')}</div>
    ${working ? `<div class="footer-acts"><span class="hint">${canReady?'Semua paket lengkap. Bungkus jadi satu dan tempel slip tiket.':'Tombol siap aktif setelah semua paket terpindai.'}</span>
      <button class="btn btn-primary" type="button" data-act="s-ready" ${canReady?'':'disabled'}><i data-lucide="check-check"></i>Siap diambil</button></div>` : ''}`;
}
async function sorterAct(a, el){
  const t = ui.sel && byId(ui.sel); if(!t) return; ui.err='';
  if(a==='s-take') aksi({aksi:'ambil', id:t.id});
  if(a==='s-bsel'){ ui.bsel = Number(el.dataset.i); render(); const r=document.getElementById('s-resi'); if(r) r.focus(); }
  if(a==='s-scan'){ const inp=document.getElementById('s-resi'); const resi=String(inp&&inp.value||'').replace(/\s/g,'');
    if(resi.length<8){ ui.err='Nomor resi terlalu pendek. Pindai ulang barcode di label.'; render(); return; }
    const r = await fetch('/api/aksi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({aksi:'scan', id:t.id, i:ui.bsel, resi})});
    const j = await r.json().catch(()=>({}));
    if(!r.ok){ ui.err = j.error || 'Resi belum tersimpan.'; render(); return; }
    if(inp){ inp.value=''; inp.dataset.fresh='1'; } ui.bsel=null; await muat(); render(); const n=document.getElementById('s-resi'); if(n && !n.disabled) n.focus(); }
  if(a==='s-undo') aksi({aksi:'ulang', id:t.id, i:Number(el.dataset.i)});
  if(a==='s-nf') aksi({aksi:'tidak_ditemukan', id:t.id, i:Number(el.dataset.i)}, `${t.no} dipindah ke Bermasalah`);
  if(a==='s-lay') aksi({aksi:'layanan', id:t.id}, `${t.no} dikoreksi ke ${LAY[t.layanan==='instant'?'sameday':'instant'].label}`);
  if(a==='s-ready'){ if(await aksi({aksi:'siap', id:t.id})){ const x=byId(t.id); toast(x&&x.loket?`${t.no} siap di Loket ${x.loket}`:`${t.no} siap, tapi belum ada loket ${LAY[t.layanan].short} yang dibuka`); } }
}

/* ---------- LOKET ---------- */
function vLoket(){
  const n = ui.loketNo; const L = S.config.lokets.find(l=>l.n===n) || {n, layanan:'instant', open:false};
  const waiting = all().filter(t=>t.status==='siap' && t.loket===n).sort((a,b)=>a.ready_at-b.ready_at);
  const pick = ui.pick && byId(ui.pick);
  return `<div class="stack" style="gap:20px">
    <div class="lk-tabs" role="group" aria-label="Pilih loket">${S.config.lokets.map(l=>`<button class="lk-tab" type="button" data-act="l-no" data-v="${l.n}" aria-pressed="${l.n===n}"><b>Loket ${l.n}</b><span>${LAY[l.layanan].short} · ${l.open?'dibuka':'ditutup'}</span></button>`).join('')}</div>
    ${!L.open?`<div class="alert a-warn"><i data-lucide="door-closed"></i><span>Loket ${n} sedang ditutup. Supervisor bisa membukanya dari tab Supervisor.</span></div>`:''}
    <div class="console">
      <section class="card panel"><div class="panel-head"><h2>Loket ${n}</h2>${layPill(L.layanan)}</div>
        <p class="hint">Hanya melayani tiket ${LAY[L.layanan].label}.</p><p class="eyebrow">Menunggu diambil · ${waiting.length}</p>
        ${waiting.length?`<div class="wlist">${waiting.map(t=>{ const left=mins(t.ready_at+batasMs()-now()); return `<div class="r"><div><b class="num" style="font-family:var(--font-display);color:var(--navy)">${esc(t.no)}</b> · ${esc(t.nama)}<div class="hint">${t.bookings.filter(b=>b.status==='dipindai').length} paket dalam 1 bungkus</div></div><span class="num ${left<=5?'late':left<=10?'soon':'hint'}">sisa ${left} mnt</span><button class="btn btn-outline btn-sm" type="button" data-act="l-pick" data-id="${t.id}">Buka</button></div>`; }).join('')}</div>`
          : empty('vidi-sleepy.png','Belum ada paket di loket ini','Tiket muncul di sini setelah sorter menandainya siap.')}
      </section>
      <section class="card detail">
        <div class="field"><label for="l-scan">Pindai slip tiket</label>
          <div class="scan"><input class="input code-input" id="l-scan" autocomplete="off" placeholder="I-0042"><button class="btn btn-primary" type="button" data-act="l-find"><i data-lucide="scan-line"></i>Cari</button></div>
          <span class="hint">Pindai barcode di slip pada paket gabungan, atau ketik nomor antrian.</span></div>
        ${ui.lerr?`<div class="alert a-bad"><i data-lucide="alert-triangle"></i><span>${esc(ui.lerr)}</span></div>`:''}
        ${pick ? loketMatch(pick) : empty('vidi-ok.png','Siap melayani driver','Hasil pindaian slip muncul di sini, lengkap dengan nama dan 4 digit HP untuk dicocokkan.')}
      </section>
    </div></div>`;
}
function loketMatch(t){
  if(t.status==='selesai') return `<div class="alert a-ok"><i data-lucide="check-circle-2"></i><span>${esc(t.no)} sudah diserahkan pukul ${hhmm(t.done_at)}.</span></div>`;
  if(t.status!=='siap') return `<div class="alert a-warn"><i data-lucide="info"></i><span>${esc(t.no)} berstatus ${esc(ST[t.status][0])}. Belum bisa diserahkan.</span></div>`;
  const left = mins(t.ready_at+batasMs()-now());
  return `<div class="match">
    <div class="panel-head"><div><p class="eyebrow">Cocokkan dengan driver di depan loket</p><div class="who">${esc(t.nama)}</div></div>${layPill(t.layanan)}</div>
    <div class="row-inline" style="justify-content:space-between;align-items:center"><div><p class="hint">4 digit terakhir HP</p><div class="hp4 num">${esc(last4(t.hp))}</div></div><div style="text-align:right"><p class="hint">Nomor antrian</p><div class="hp4 num" style="letter-spacing:0">${esc(t.no)}</div></div></div>
    <div class="blist">${t.bookings.filter(b=>b.status==='dipindai').map(b=>`<div class="brow" style="grid-template-columns:auto minmax(0,1fr)"><span class="k" style="font-size:18px">${esc(b.kode)}</span><span class="rs">resi <b>${esc(b.resi)}</b></span></div>`).join('')}</div>
    <p class="hint">Siap sejak ${hhmm(t.ready_at)} · sisa waktu ambil <b class="num">${left}</b> menit</p>
    <button class="btn btn-primary btn-block" type="button" data-act="l-confirm" data-id="${t.id}"><i data-lucide="hand-helping"></i>Konfirmasi serah terima</button></div>`;
}
function loketAct(a, el){
  ui.lerr='';
  if(a==='l-no'){ ui.loketNo=Number(el.dataset.v); ls('qhouse.loket', String(ui.loketNo)); ui.pick=null; render(); }
  if(a==='l-pick'){ ui.pick=el.dataset.id; render(); }
  if(a==='l-find'){
    const v = String((document.getElementById('l-scan')||{}).value||'').toUpperCase().replace(/\s/g,'');
    const m = v.match(/^([IS])-?0*(\d{1,4})$/); const key = m ? m[1]+'-'+m[2].padStart(4,'0') : v;
    const cands = all().filter(t=>t.no===key).sort((a,b)=>b.created_at-a.created_at);
    const t = cands.find(x=>['menunggu','disiapkan','siap','bermasalah'].includes(x.status)) || cands[0];
    if(!t){ ui.lerr=`Tiket ${v||'kosong'} tidak ditemukan. Cek nomor di slip.`; ui.pick=null; render(); return; }
    if(t.status==='siap' && t.loket!==ui.loketNo){ ui.lerr=`${t.no} untuk Loket ${t.loket||'–'} (${LAY[t.layanan].short}). Arahkan driver ke sana.`; ui.pick=null; render(); return; }
    ui.pick=t.id; const inp=document.getElementById('l-scan'); if(inp){ inp.value=''; inp.dataset.fresh='1'; } render();
  }
  if(a==='l-confirm'){ const t=byId(el.dataset.id); aksi({aksi:'serah', id:el.dataset.id, loket:ui.loketNo}, t?`${t.no} selesai diserahkan`:''); }
}

/* ---------- SUPERVISOR ---------- */
function vSupervisor(){
  const T = all(), q = queue(), qi = q.filter(t=>t.layanan==='instant').length;
  const ready = T.filter(t=>t.status==='siap'), bad = T.filter(t=>t.status==='bermasalah');
  const hari = new Date(now()).toDateString();
  const done = T.filter(t=>t.status==='selesai' && new Date(t.done_at||0).toDateString()===hari);
  const avg = done.length ? Math.round(done.reduce((s,t)=>s+(t.done_at-t.created_at),0)/done.length/60000) : null;
  const toRack = T.filter(t=>t.status==='dibatalkan' && t.ready_at && !t.returned);
  const qq = ui.q.trim().toUpperCase();
  const rows = T.filter(t=>new Date(t.created_at).toDateString()===hari).filter(t=>!qq || [t.no,t.nama,t.hp,...t.bookings.map(b=>b.kode),...t.bookings.map(b=>b.resi||'')].join(' ').toUpperCase().includes(qq)).sort((a,b)=>b.created_at-a.created_at);
  const kpi=(l,v,s)=>`<div class="card kpi"><span class="eyebrow">${l}</span><span class="v num">${v}</span><span class="s">${s}</span></div>`;
  return `<div class="kpis">${kpi('Menunggu antrian', q.length, `Instant ${qi} · Sameday ${q.length-qi}`)}${kpi('Siap diambil', ready.length, `di ${new Set(ready.map(t=>t.loket)).size} loket`)}${kpi('Bermasalah', bad.length, bad.length?'perlu keputusan':'semua lancar')}${kpi('Selesai hari ini', done.length, avg!=null?`rata-rata ${avg} menit dari tiket dibuat`:'belum ada')}</div>
    <div class="grid2"><div class="stack">
      <section class="card panel"><div class="panel-head"><h2>Loket dan layanannya</h2></div>
        <p class="hint">Tiket diarahkan ke loket sesuai layanannya, bergiliran bila ada lebih dari satu loket.</p>
        <div class="lcfg">${S.config.lokets.map((l,i)=>`<div class="lcard ${l.open?'':'off'}"><div class="top2"><b>Loket ${l.n}</b><button class="switch" type="button" role="switch" aria-checked="${l.open}" aria-label="Loket ${l.n} dibuka" data-act="c-open" data-i="${i}"></button></div>
          <select class="input" data-change="c-lay" data-i="${i}" aria-label="Layanan Loket ${l.n}">${['instant','sameday'].map(x=>`<option value="${x}" ${l.layanan===x?'selected':''}>${LAY[x].label}</option>`).join('')}</select>
          <span class="hint">${l.open?'Dibuka':'Ditutup'} · ${T.filter(t=>t.status==='siap'&&t.loket===l.n).length} paket menunggu</span></div>`).join('')}</div>
        ${['instant','sameday'].filter(x=>!S.config.lokets.some(l=>l.open&&l.layanan===x)).map(x=>`<div class="alert a-warn"><i data-lucide="alert-triangle"></i><span>Belum ada loket ${LAY[x].label} yang dibuka. Tiket ${LAY[x].short} yang siap tidak punya tujuan.</span></div>`).join('')}
        <div class="row-inline"><div class="field" style="flex:1;min-width:160px"><label for="cfg-batas">Batas ambil (menit)</label><input class="input num" id="cfg-batas" type="number" min="5" max="240" value="${esc(S.config.batasMenit)}"></div><button class="btn btn-outline" type="button" data-act="c-batas">Simpan</button></div>
      </section>
      <section class="card panel"><div class="panel-head"><h2>Tiket bermasalah</h2><span class="pill ${bad.length?'p-bad':'p-done'} num">${bad.length}</span></div>
        ${bad.length?bad.map(t=>`<div class="prob"><div class="panel-head"><div><b style="font-family:var(--font-display);color:var(--navy);font-size:18px">${esc(t.no)}</b> · ${esc(t.nama)} · ${esc(maskHp(t.hp))}</div>${layPill(t.layanan)}</div>
            ${t.bookings.map((b,i)=>b.status==='tidak_ditemukan'?`<div class="bk"><span><b style="letter-spacing:.1em">${esc(b.kode)}</b> tidak ditemukan sorter</span><span class="row-inline"><button class="btn btn-outline btn-sm" type="button" data-act="v-retry" data-id="${t.id}" data-i="${i}">Cari ulang</button><button class="btn btn-outline btn-sm" type="button" data-act="v-out" data-id="${t.id}" data-i="${i}">Keluarkan</button></span></div>`:'').join('')}
            <div><button class="btn btn-danger btn-sm" type="button" data-act="v-cancel" data-id="${t.id}">Batalkan tiket</button></div></div>`).join('') : empty('vidi-ok.png','Tidak ada tiket tertahan')}
      </section>
      ${toRack.length?`<section class="card panel"><div class="panel-head"><h2>Kembalikan ke rak</h2></div><p class="hint">Tiket dibatalkan setelah paket dibungkus. Bongkar dan kembalikan paket ke rak.</p>
        ${toRack.map(t=>`<div class="row-inline" style="justify-content:space-between;align-items:center;border-top:1px solid var(--border-subtle);padding-top:10px"><span><b class="num">${esc(t.no)}</b> · Loket ${esc(t.loket||'–')} · ${esc(t.cancel_reason||'dibatalkan')}</span><button class="btn btn-outline btn-sm" type="button" data-act="v-returned" data-id="${t.id}">Sudah dikembalikan</button></div>`).join('')}</section>`:''}
    </div><div class="stack">
      <section class="card panel"><div class="panel-head"><h2>Tiket hari ini</h2><span class="hint num">${rows.length} tiket</span></div>
        <input class="input" id="sv-q" placeholder="Cari nomor antrian, nama, HP, booking, atau resi" value="${esc(ui.q)}">
        ${rows.length?`<div class="tbl-wrap"><table><thead><tr><th>No.</th><th>Driver</th><th>Booking</th><th>Status</th><th>Loket</th></tr></thead><tbody>
          ${rows.map(t=>`<tr><td class="no num">${esc(t.no)}</td><td>${esc(t.nama)}<div class="hint">${hhmm(t.created_at)}${t.sorter?' · '+esc(t.sorter):''}</div></td><td>${t.bookings.map(b=>esc(b.kode)).join(', ')}</td><td>${stPill(t.status)}</td><td class="num">${t.loket?esc(t.loket):'–'}</td></tr>`).join('')}
        </tbody></table></div>` : empty('vidi-sleepy.png', qq?'Tidak ada yang cocok':'Belum ada tiket hari ini', qq?'Coba kata kunci lain.':'Tiket muncul di sini begitu driver mengirim dari barcode gudang.')}
      </section>
    </div></div>`;
}
function svAct(a, el){
  const i = Number(el.dataset.i), t = el.dataset.id && byId(el.dataset.id);
  if(a==='c-open') aksi({aksi:'loket_buka', i});
  if(a==='c-batas'){ const v=Number((document.getElementById('cfg-batas')||{}).value); aksi({aksi:'batas', menit:v}); }
  if(a==='v-retry') aksi({aksi:'cari_ulang', id:el.dataset.id, i});
  if(a==='v-out') aksi({aksi:'keluarkan', id:el.dataset.id, i});
  if(a==='v-cancel' && t && confirm(`Batalkan tiket ${t.no}?`)) aksi({aksi:'batal', id:t.id}, `${t.no} dibatalkan`);
  if(a==='v-returned') aksi({aksi:'kembali_rak', id:el.dataset.id});
}

/* ---------- BARCODE ---------- */
const qrUrl = l => S.publicUrl + '/antri/' + l;
function qrMatrix(text){
  if(typeof window.qrcode!=='function') return null;
  try{ const q=window.qrcode(0,'M'); q.addData(text); q.make(); const n=q.getModuleCount(), m=[]; for(let r=0;r<n;r++){ const row=[]; for(let c=0;c<n;c++) row.push(q.isDark(r,c)); m.push(row); } return m; }catch(e){ return null; }
}
function qrSvg(text, label){
  const m = qrMatrix(text); if(!m) return empty('vidi-sleepy.png','Barcode belum bisa digambar','Muat ulang halaman.');
  const n=m.length, q=4, size=n+q*2; let d='';
  m.forEach((row,r)=>row.forEach((on,c)=>{ if(on) d+=`M${c+q} ${r+q}h1v1h-1z`; }));
  return `<svg viewBox="0 0 ${size} ${size}" role="img" aria-label="${esc(label)}" shape-rendering="crispEdges"><rect width="${size}" height="${size}" fill="#FFFFFF"/><path d="${d}" fill="#00045B"/></svg>`;
}
function vBarcode(){
  const poster = l => `<article class="card poster ${l}">
    <div class="band"><span class="eyebrow">Gudang · VitaminDiskon</span><h2>${LAY[l].label}</h2><p>${l==='instant'?'Termasuk Instant Prioritas':'Pengiriman di hari yang sama'}</p></div>
    <div class="qr">${qrSvg(qrUrl(l), 'Barcode antrian '+LAY[l].label)}</div>
    <div class="how"><b style="color:var(--navy)">Scan untuk ambil nomor antrian</b><ol><li>Scan barcode ini dengan kamera HP.</li><li>Isi nama, nomor HP, dan semua nomor booking.</li><li>Tunggu sampai HP menunjukkan nomor loket.</li></ol><span class="url">${esc(qrUrl(l))}</span></div>
    <div class="acts"><button class="btn btn-primary btn-sm" type="button" data-act="b-dl" data-v="${l}"><i data-lucide="download"></i>Unduh PNG</button>
      <button class="btn btn-outline btn-sm" type="button" data-act="b-copy" data-v="${l}"><i data-lucide="copy"></i>Salin tautan</button>
      <a class="btn btn-quiet btn-sm" href="/antri/${l}" target="_blank" rel="noopener"><i data-lucide="smartphone"></i>Buka halaman driver</a></div></article>`;
  return `<div class="stack" style="gap:20px"><div class="panel-head"><div><p class="eyebrow">Barcode statis</p><h2>Dua barcode untuk pintu gudang</h2></div></div>
    <div class="alert a-info"><i data-lucide="info"></i><span>Tempel barcode Instant dan Sameday di tempat terpisah. Layanan tiket otomatis mengikuti barcode yang di-scan. Halaman driver terbuka tanpa akun Voyage.</span></div>
    <div class="posters">${poster('instant')}${poster('sameday')}</div></div>`;
}
async function unduh(l){
  const m = qrMatrix(qrUrl(l)); if(!m){ toast('Barcode belum bisa digambar.'); return; }
  const W=1240, H=1754, c=document.createElement('canvas'); c.width=W; c.height=H; const x=c.getContext('2d'), inst = l==='instant';
  x.fillStyle='#FFFFFF'; x.fillRect(0,0,W,H); x.fillStyle= inst?'#2A52A4':'#E9F9FE'; x.fillRect(0,0,W,420);
  try{ await document.fonts.ready; }catch(e){}
  if(inst){ const img=document.querySelector('.brand img'); try{ if(img && img.complete) x.drawImage(img,80,70,238,70); }catch(e){} }
  x.fillStyle= inst?'#FFFFFF':'#00045B'; x.font='700 28px "Plus Jakarta Sans", sans-serif'; x.fillText('GUDANG · AMBIL NOMOR ANTRIAN', 80, inst?210:140);
  x.font='800 120px "Funnel Display", "Plus Jakarta Sans", sans-serif'; x.fillText(LAY[l].label, 74, inst?340:290);
  if(!inst){ x.font='500 34px "Plus Jakarta Sans", sans-serif'; x.fillText('Pengiriman di hari yang sama', 80, 360); }
  const n=m.length, q=4, cell=Math.floor(860/(n+q*2)), size=cell*(n+q*2), ox=(W-size)/2, oy=500;
  x.fillStyle='#00045B'; m.forEach((row,r)=>row.forEach((on,cc)=>{ if(on) x.fillRect(ox+(cc+q)*cell, oy+(r+q)*cell, cell, cell); }));
  x.textAlign='center'; x.font='800 54px "Funnel Display", "Plus Jakarta Sans", sans-serif'; x.fillText('Scan untuk ambil nomor antrian', W/2, oy+size+90);
  x.font='500 32px "Plus Jakarta Sans", sans-serif'; x.fillStyle='#2C3552';
  ['Isi nama, nomor HP, dan semua nomor booking.','Tunggu sampai HP menunjukkan nomor loket.'].forEach((s,i)=>x.fillText(s, W/2, oy+size+150+i*48));
  x.font='500 22px "Plus Jakarta Sans", sans-serif'; x.fillStyle='#7A8899'; x.fillText(qrUrl(l), W/2, H-60);
  c.toBlob(b => { const u=URL.createObjectURL(b), a=document.createElement('a'); a.href=u; a.download=`qhouse-barcode-${l}.png`; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(u),4000); toast('Barcode diunduh'); }, 'image/png');
}

/* ---------- events ---------- */
document.addEventListener('click', e => {
  const tab = e.target.closest('[data-tab]'); if(tab){ e.preventDefault(); ui.tab=tab.dataset.tab; history.replaceState(null,'','/'+ui.tab); render(); window.scrollTo(0,0); return; }
  const el = e.target.closest('[data-act]'); if(!el || el.disabled) return; const a = el.dataset.act;
  if(a==='s-tab'){ ui.sorterTab=el.dataset.v; ui.sel=null; render(); }
  else if(a==='s-sel'){ ui.sel=el.dataset.id; ui.bsel=null; ui.err=''; render(); }
  else if(a.startsWith('s-')) sorterAct(a, el);
  else if(a.startsWith('l-')) loketAct(a, el);
  else if(a.startsWith('c-')||a.startsWith('v-')) svAct(a, el);
  else if(a==='b-dl') unduh(el.dataset.v);
  else if(a==='b-copy') navigator.clipboard.writeText(qrUrl(el.dataset.v)).then(()=>toast('Tautan disalin'), ()=>toast(qrUrl(el.dataset.v)));
});
document.addEventListener('keydown', e => {
  if(e.key!=='Enter') return; const id = e.target && e.target.id;
  if(id==='s-resi'){ e.preventDefault(); sorterAct('s-scan', e.target); }
  if(id==='l-scan'){ e.preventDefault(); loketAct('l-find', e.target); }
  if(id==='cfg-batas'){ e.preventDefault(); svAct('c-batas', e.target); }
});
document.addEventListener('input', e => { if(e.target.id==='sv-q'){ ui.q=e.target.value; clearTimeout(render._t); render._t=setTimeout(render,200); } });
document.addEventListener('change', e => { const t=e.target; if(t.dataset.change==='c-lay') aksi({aksi:'loket_layanan', i:Number(t.dataset.i), layanan:t.value}); });

(async () => { await muat(); render(); setInterval(async () => { await muat(); const a=document.activeElement; if(!(a && (a.tagName==='INPUT'||a.tagName==='SELECT'))) render(); }, 4000); })();
})();
