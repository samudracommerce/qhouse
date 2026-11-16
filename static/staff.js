/* Q-House — konsol STAF (lewat voyage-gate). Tab mengikuti peran akun (me.peran): sorter, loket, supervisor; Barcode
   untuk supervisor. Data dari /api/state?tab=<tab> (polling 4 detik; tab=sorter = hadir sebagai sorter aktif -> menerima
   tiket otomatis); setiap perubahan lewat /api/aksi (server memeriksa peran). Alur versi 2 (Sheva 12 Nov 2026). */
// Prefix jalur (Q-House = fitur Wareflow di /qhouse, Sheva 16 Nov 2026); kosong = di akar domain. Diisi server lewat meta qh-base.
const BASE = (document.querySelector('meta[name="qh-base"]') || {}).content || '';
const tabDariJalur = () => location.pathname.slice(BASE.length).replace(/^\/+|\/+$/g, '');
(function(){
'use strict';
const LAY = {instant:{label:'SPX Instant', short:'Instant', cls:'p-instant'}, sameday:{label:'SPX Sameday', short:'Sameday', cls:'p-sameday'}};
const ST = {menunggu:['Menunggu sorter','p-wait'], disiapkan:['Sedang disiapkan','p-prep'], siap:['Siap di loket','p-ready'],
            selesai:['Selesai','p-done'], bermasalah:['Bermasalah','p-bad'], dibatalkan:['Dibatalkan','p-cancel']};
const HASIL = {cocok:['Cocok','p-done'], tidak_ditemukan:['Tidak ditemukan','p-bad'], belum_diserahkan:['Belum diserahkan ke kurir','p-prep'], batal:['Batal di BigSeller','p-bad']};
const TABS = [['sorter','package-search','Sorter','sorter'],['loket','store','Loket','loket'],['supervisor','layout-dashboard','Supervisor','supervisor'],['barcode','qr-code','Barcode','supervisor']];
let S = null, ok = false;
const ui = {tab:null, loketNo:Number(ls('qhouse.loket'))||1, pick:null, lerr:'', q:'', vtgl:''};

function ls(k,v){ try{ if(v===undefined) return localStorage.getItem(k); localStorage.setItem(k,v); }catch(e){ return null; } }
const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hhmm = ms => { if(!ms) return '–'; const d=new Date(ms); return String(d.getHours()).padStart(2,'0')+'.'+String(d.getMinutes()).padStart(2,'0'); };
const mins = ms => Math.max(0, Math.round(ms/60000));
const now = () => S ? S.now + (Date.now() - S._at) : Date.now();
const last4 = hp => String(hp||'').replace(/\D/g,'').slice(-4) || '––––';
const maskHp = hp => { const d=String(hp||'').replace(/\D/g,''); return d.length<6? d : d.slice(0,4)+'••••'+d.slice(-2); };
const layPill = l => `<span class="pill ${LAY[l]?.cls||''}">${esc(LAY[l]?.label||l)}</span>`;
const stPill = s => `<span class="pill ${ST[s]?.[1]||''}">${esc(ST[s]?.[0]||s)}</span>`;
const boleh = p => S && S.me.peran.includes(p);
const all = () => S ? S.tickets : [];
const byId = id => all().find(t => t.id===id);
const batasMs = () => (Number(S.config.batasMenit)||30)*60000;
const empty = (img, b, s) => `<div class="empty"><img src="${BASE}/static/assets/${img}" alt="" width="96"><b>${b}</b>${s?`<span>${s}</span>`:''}</div>`;
function toast(msg){ const el=document.getElementById('toast'); el.textContent=msg; el.hidden=false; clearTimeout(toast._t); toast._t=setTimeout(()=>{el.hidden=true;},3200); }

async function muat(){
  try{ const r = await fetch(BASE + '/api/state?tab=' + encodeURIComponent(ui.tab||''), {cache:'no-store'}); if(!r.ok) throw new Error(r.status); S = await r.json(); S._at = Date.now(); ok = true; }
  catch(e){ ok = false; }
}
async function kirim(body){
  const r = await fetch(BASE + '/api/aksi', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const j = await r.json().catch(()=>({})); return {ok:r.ok, j};
}
async function aksi(body, pesan){
  try{ const {ok:okk, j} = await kirim(body);
    if(!okk){ toast(j.error || 'Perubahan belum tersimpan. Coba lagi.'); await muat(); render(); return false; }
    if(pesan || j.pesan) toast(pesan || j.pesan); await muat(); render(); return true;
  }catch(e){ toast('Koneksi terputus. Coba lagi.'); return false; }
}

/* ---------- shell ---------- */
function render(){
  const view = document.getElementById('view'), dot = document.getElementById('modedot'), tx = document.getElementById('modetext');
  dot.className = 'dot ' + (ok ? 'live' : 'warn');
  if(!S){ tx.textContent = 'Belum tersambung ke server. Mencoba lagi…'; view.innerHTML=''; return; }
  const tabs = TABS.filter(t => boleh(t[3]));
  if(!ui.tab || !tabs.some(t=>t[0]===ui.tab)){ const h=tabDariJalur(); ui.tab = (tabs.find(t=>t[0]===h)||tabs[0]||[null])[0]; }
  const aktif = S.sorterAktif.some(s => s.email===S.me.email);
  tx.textContent = ok ? `${S.me.nama} · ${S.me.peran.length ? S.me.peran.join(', ') : 'lihat saja'}${ui.tab==='sorter'?(aktif?' · aktif menerima tiket':' · menyambung…'):''} · diperbarui otomatis` : 'Koneksi terputus. Mencoba lagi…';
  document.getElementById('roles').innerHTML = tabs.map(t => `<a href="${BASE}/${t[0]}" data-tab="${t[0]}" ${t[0]===ui.tab?'aria-current="page"':''}><i data-lucide="${t[1]}"></i><span>${t[2]}</span></a>`).join('');
  const keep = {}; view.querySelectorAll('input[id]').forEach(el => keep[el.id] = el.value);
  const act = document.activeElement && document.activeElement.id;
  view.innerHTML = !ui.tab ? `<div class="card panel">${empty('vidi-sleepy.png','Akunmu belum punya peran di Q-House','Minta kepala divisi memberi peran Sorter, Loket, atau Supervisor lewat Voyage › Manage › Akses App › Wareflow.')}</div>`
    : ({sorter:vSorter, loket:vLoket, supervisor:vSupervisor, barcode:vBarcode}[ui.tab])();
  Object.keys(keep).forEach(id => { const el = document.getElementById(id); if(el && !(id==='l-pesanan' && ui._kosongkan)) el.value = keep[id]; });
  ui._kosongkan = false;
  if(act){ const el = document.getElementById(act); if(el) el.focus({preventScroll:true}); }
  if(window.lucide) window.lucide.createIcons({attrs:{'stroke-width':2}});
}

/* ---------- SORTER: tiket dibagikan otomatis; sorter hanya menekan Done ---------- */
function vSorter(){
  const saya = all().filter(t => t.status==='disiapkan' && t.sorter_email===S.me.email).sort((a,b)=>a.taken_at-b.taken_at);
  const menunggu = all().filter(t => t.status==='menunggu').length;
  return `<div class="console">
    <section class="card panel">
      <div class="panel-head"><h2>Tiketku</h2><span class="pill p-prep num">${saya.length}</span></div>
      <p class="hint">Tiket dari driver dibagikan otomatis ke sorter yang membuka halaman ini. Siapkan paketnya, lalu tekan <b>Done</b> — tiket pindah ke loket.</p>
      <div class="mini">
        <div class="r"><span>Sorter aktif</span><b>${S.sorterAktif.length ? S.sorterAktif.map(s=>esc(s.nama)).join(', ') : '–'}</b></div>
        <div class="r"><span>Antrean belum terbagi</span><b class="num">${menunggu}</b></div>
      </div>
    </section>
    <section class="card detail">
      ${saya.length ? `<div class="stack">${saya.map(t => `<div class="prob" style="border-color:var(--border)">
          <div class="panel-head"><div><b class="num" style="font-family:var(--font-display);font-size:26px;color:var(--navy)">${esc(t.no)}</b>
            <div class="meta">${esc(t.nama)} · ${t.bookings.length} paket · dibagikan ${hhmm(t.taken_at)}</div></div>${layPill(t.layanan)}</div>
          <div class="codes">${t.bookings.map(b=>`<div class="row"><span class="k">${esc(b.kode)}</span><span class="s">nomor booking</span></div>`).join('')}</div>
          <button class="btn btn-primary btn-block" type="button" data-act="s-done" data-id="${t.id}"><i data-lucide="check-check"></i>Done — kirim ke loket</button>
        </div>`).join('')}</div>`
        : empty('vidi-sleepy.png','Belum ada tiket untukmu','Biarkan halaman ini terbuka. Tiket baru dari driver akan muncul di sini otomatis.')}
    </section></div>`;
}

/* ---------- LOKET: pindai nomor pesanan tiap paket, lalu serah terima ---------- */
function vLoket(){
  const n = ui.loketNo; const L = S.config.lokets.find(l=>l.n===n) || {n, layanan:'instant', open:false};
  const waiting = all().filter(t=>t.status==='siap' && t.loket===n).sort((a,b)=>a.ready_at-b.ready_at);
  if(ui.pick && !waiting.some(t=>t.id===ui.pick) && !(byId(ui.pick)||{}).done_at) ui.pick = null;
  const pick = ui.pick && byId(ui.pick);
  return `<div class="stack" style="gap:20px">
    <div class="lk-tabs" role="group" aria-label="Pilih loket">${S.config.lokets.map(l=>`<button class="lk-tab" type="button" data-act="l-no" data-v="${l.n}" aria-pressed="${l.n===n}"><b>Loket ${l.n}</b><span>${LAY[l.layanan].short} · ${l.open?'dibuka':'ditutup'}</span></button>`).join('')}</div>
    ${!L.open?`<div class="alert a-warn"><i data-lucide="door-closed"></i><span>Loket ${n} sedang ditutup. Supervisor bisa membukanya dari tab Supervisor.</span></div>`:''}
    <div class="console">
      <section class="card panel"><div class="panel-head"><h2>Loket ${n}</h2>${layPill(L.layanan)}</div>
        <p class="eyebrow">Menunggu diambil · ${waiting.length}</p>
        ${waiting.length?`<div class="wlist">${waiting.map(t=>{ const left=mins(t.ready_at+batasMs()-now()); return `<div class="r"><div><b class="num" style="font-family:var(--font-display);color:var(--navy)">${esc(t.no)}</b> · ${esc(t.nama)}<div class="hint">${t.bookings.length} paket · dipindai ${t.pesanan.length}</div></div><span class="num ${left<=5?'late':left<=10?'soon':'hint'}">sisa ${left} mnt</span><button class="btn btn-outline btn-sm" type="button" data-act="l-pick" data-id="${t.id}">Buka</button></div>`; }).join('')}</div>`
          : empty('vidi-sleepy.png','Belum ada paket di loket ini','Tiket muncul di sini setelah sorter menekan Done.')}
      </section>
      <section class="card detail">
        <div class="field"><label for="l-scan">Cari tiket driver</label>
          <div class="scan"><input class="input code-input" id="l-scan" autocomplete="off" placeholder="I-0042"><button class="btn btn-primary" type="button" data-act="l-find"><i data-lucide="search"></i>Cari</button></div>
          <span class="hint">Ketik nomor antrian di HP driver, atau pilih dari daftar.</span></div>
        ${ui.lerr?`<div class="alert a-bad"><i data-lucide="alert-triangle"></i><span>${esc(ui.lerr)}</span></div>`:''}
        ${pick ? loketMatch(pick) : empty('vidi-ok.png','Siap melayani driver','Buka tiket, cocokkan nama & 4 digit HP, pindai nomor pesanan tiap paket, lalu serah terima.')}
      </section>
    </div></div>`;
}
function loketMatch(t){
  if(t.status==='selesai') return `<div class="alert a-ok"><i data-lucide="check-circle-2"></i><span>${esc(t.no)} sudah diserahkan pukul ${hhmm(t.done_at)}.</span></div>`;
  if(t.status!=='siap') return `<div class="alert a-warn"><i data-lucide="info"></i><span>${esc(t.no)} berstatus ${esc(ST[t.status][0])}. Belum bisa diserahkan.</span></div>`;
  const left = mins(t.ready_at+batasMs()-now()), n = t.bookings.length, d = t.pesanan.length, lengkap = d===n;
  return `<div class="match">
    <div class="panel-head"><div><p class="eyebrow">Cocokkan dengan driver di depan loket</p><div class="who">${esc(t.nama)}</div></div>${layPill(t.layanan)}</div>
    <div class="row-inline" style="justify-content:space-between;align-items:center"><div><p class="hint">4 digit terakhir HP</p><div class="hp4 num">${esc(last4(t.hp))}</div></div><div style="text-align:right"><p class="hint">Nomor antrian</p><div class="hp4 num" style="letter-spacing:0">${esc(t.no)}</div></div></div>
    <div class="scanbox"><label for="l-pesanan" class="eyebrow">Pindai nomor pesanan · ${d}/${n} paket</label>
      <div class="scan"><input class="input num" id="l-pesanan" autocomplete="off" placeholder="Pindai barcode nomor pesanan di label" ${lengkap?'disabled':''}><button class="btn btn-primary" type="button" data-act="l-scanno" ${lengkap?'disabled':''}><i data-lucide="scan-line"></i>Simpan</button></div>
      <span class="hint">Satu pindaian per paket. Jumlahnya harus sama dengan ${n} nomor booking driver.</span></div>
    ${d?`<div class="blist">${t.pesanan.map((p,i)=>`<div class="brow" style="grid-template-columns:auto minmax(0,1fr) auto"><span class="k" style="font-size:15px;letter-spacing:.04em;min-width:0">${i+1}.</span><span class="rs"><b>${esc(p)}</b></span><button class="btn btn-quiet btn-sm" type="button" data-act="l-hapus" data-i="${i}">Hapus</button></div>`).join('')}</div>`:''}
    <p class="hint">Siap sejak ${hhmm(t.ready_at)} · sisa waktu ambil <b class="num">${left}</b> menit</p>
    <button class="btn btn-primary btn-block" type="button" data-act="l-confirm" data-id="${t.id}" ${lengkap?'':'disabled'}><i data-lucide="hand-helping"></i>Konfirmasi serah terima</button></div>`;
}
async function loketAct(a, el){
  ui.lerr='';
  if(a==='l-no'){ ui.loketNo=Number(el.dataset.v); ls('qhouse.loket', String(ui.loketNo)); ui.pick=null; render(); }
  if(a==='l-pick'){ ui.pick=el.dataset.id; render(); const p=document.getElementById('l-pesanan'); if(p && !p.disabled) p.focus(); }
  if(a==='l-find'){
    const v = String((document.getElementById('l-scan')||{}).value||'').toUpperCase().replace(/\s/g,'');
    const m = v.match(/^([IS])-?0*(\d{1,4})$/); const key = m ? m[1]+'-'+m[2].padStart(4,'0') : v;
    const t = all().filter(t=>t.no===key).sort((a,b)=>b.created_at-a.created_at)[0];
    if(!t){ ui.lerr=`Tiket ${v||'kosong'} tidak ditemukan. Cek nomor di HP driver.`; ui.pick=null; render(); return; }
    if(t.status==='siap' && t.loket!==ui.loketNo){ ui.lerr=`${t.no} untuk Loket ${t.loket||'–'} (${LAY[t.layanan].short}). Arahkan driver ke sana.`; ui.pick=null; render(); return; }
    ui.pick=t.id; const inp=document.getElementById('l-scan'); if(inp) inp.value=''; render(); const p=document.getElementById('l-pesanan'); if(p && !p.disabled) p.focus();
  }
  if(a==='l-scanno' && ui.pick){
    const inp=document.getElementById('l-pesanan'); const no=String(inp&&inp.value||'').trim();
    if(!no) return;
    const {ok:okk, j} = await kirim({aksi:'scan', id:ui.pick, no});
    if(!okk){ ui.lerr = j.error || 'Nomor pesanan belum tersimpan.'; }
    else ui._kosongkan = true;
    await muat(); render(); const p=document.getElementById('l-pesanan'); if(p && !p.disabled){ if(okk) p.value=''; p.focus(); }
  }
  if(a==='l-hapus' && ui.pick) aksi({aksi:'hapus_scan', id:ui.pick, i:Number(el.dataset.i)});
  if(a==='l-confirm'){ const t=byId(el.dataset.id); aksi({aksi:'serah', id:el.dataset.id, loket:ui.loketNo}, t?`${t.no} selesai diserahkan`:''); }
}

/* ---------- SUPERVISOR ---------- */
function vSupervisor(){
  const T = all(), q = T.filter(t=>t.status==='menunggu'), dis = T.filter(t=>t.status==='disiapkan');
  const ready = T.filter(t=>t.status==='siap'), hari = new Date(now()).toDateString();
  const done = T.filter(t=>t.status==='selesai' && new Date(t.done_at||0).toDateString()===hari);
  const avg = done.length ? Math.round(done.reduce((s,t)=>s+(t.done_at-t.created_at),0)/done.length/60000) : null;
  const toRack = T.filter(t=>t.status==='dibatalkan' && t.ready_at && !t.returned);
  const qq = ui.q.trim().toUpperCase();
  const rows = T.filter(t=>new Date(t.created_at).toDateString()===hari).filter(t=>!qq || [t.no,t.nama,t.hp,t.sorter||'',...t.bookings.map(b=>b.kode),...t.pesanan].join(' ').toUpperCase().includes(qq)).sort((a,b)=>b.created_at-a.created_at);
  const kpi=(l,v,s)=>`<div class="card kpi"><span class="eyebrow">${l}</span><span class="v num">${v}</span><span class="s">${s}</span></div>`;
  const V = S.validasi, vl = V.log[0];
  const kemarin = new Date(now()-86400000); const kmr = `${kemarin.getFullYear()}-${String(kemarin.getMonth()+1).padStart(2,'0')}-${String(kemarin.getDate()).padStart(2,'0')}`;
  return `<div class="kpis">${kpi('Menunggu sorter', q.length, `${S.sorterAktif.length} sorter aktif`)}${kpi('Disiapkan sorter', dis.length, dis.length?`${new Set(dis.map(t=>t.sorter)).size} sorter`:'–')}${kpi('Siap di loket', ready.length, `di ${new Set(ready.map(t=>t.loket)).size} loket`)}${kpi('Selesai hari ini', done.length, avg!=null?`rata-rata ${avg} menit dari tiket dibuat`:'belum ada')}</div>
    <div class="grid2"><div class="stack">
      <section class="card panel"><div class="panel-head"><h2>Sorter aktif</h2><span class="pill p-wait num">${S.sorterAktif.length}</span></div>
        <p class="hint">Sorter yang sedang membuka tab Sorter menerima tiket otomatis (beban paling sedikit dulu). Bila tabnya tertutup lebih dari 5 menit, tiketnya kembali ke antrean.</p>
        ${S.sorterAktif.length?`<div class="mini">${S.sorterAktif.map(s=>`<div class="r"><span>${esc(s.nama)}</span><b class="num">${dis.filter(t=>t.sorter_email===s.email).length} tiket</b></div>`).join('')}</div>`:`<div class="alert a-warn"><i data-lucide="alert-triangle"></i><span>Belum ada sorter aktif. Tiket driver menunggu sampai ada sorter membuka tab Sorter.</span></div>`}
        ${dis.length?`<p class="eyebrow">Sedang disiapkan</p>${dis.map(t=>`<div class="row-inline" style="justify-content:space-between;align-items:center;border-top:1px solid var(--border-subtle);padding-top:8px"><span><b class="num">${esc(t.no)}</b> · ${esc(t.sorter||'–')} · ${mins(now()-(t.taken_at||t.created_at))} mnt</span><button class="btn btn-quiet btn-sm" type="button" data-act="v-lepas" data-id="${t.id}">Tarik ke antrean</button></div>`).join('')}`:''}
      </section>
      <section class="card panel"><div class="panel-head"><h2>Loket dan layanannya</h2></div>
        <div class="lcfg">${S.config.lokets.map((l,i)=>`<div class="lcard ${l.open?'':'off'}"><div class="top2"><b>Loket ${l.n}</b><button class="switch" type="button" role="switch" aria-checked="${l.open}" aria-label="Loket ${l.n} dibuka" data-act="c-open" data-i="${i}"></button></div>
          <select class="input" data-change="c-lay" data-i="${i}" aria-label="Layanan Loket ${l.n}">${['instant','sameday'].map(x=>`<option value="${x}" ${l.layanan===x?'selected':''}>${LAY[x].label}</option>`).join('')}</select>
          <span class="hint">${l.open?'Dibuka':'Ditutup'} · ${T.filter(t=>t.status==='siap'&&t.loket===l.n).length} paket menunggu</span></div>`).join('')}</div>
        ${['instant','sameday'].filter(x=>!S.config.lokets.some(l=>l.open&&l.layanan===x)).map(x=>`<div class="alert a-warn"><i data-lucide="alert-triangle"></i><span>Belum ada loket ${LAY[x].label} yang dibuka.</span></div>`).join('')}
        <div class="row-inline"><div class="field" style="flex:1;min-width:160px"><label for="cfg-batas">Batas ambil (menit)</label><input class="input num" id="cfg-batas" type="number" min="5" max="240" value="${esc(S.config.batasMenit)}"></div><button class="btn btn-outline" type="button" data-act="c-batas">Simpan</button></div>
      </section>
      ${toRack.length?`<section class="card panel"><div class="panel-head"><h2>Kembalikan ke rak</h2></div><p class="hint">Tiket dibatalkan setelah paket siap. Kembalikan paket ke rak.</p>
        ${toRack.map(t=>`<div class="row-inline" style="justify-content:space-between;align-items:center;border-top:1px solid var(--border-subtle);padding-top:10px"><span><b class="num">${esc(t.no)}</b> · Loket ${esc(t.loket||'–')} · ${esc(t.cancel_reason||'dibatalkan')}</span><button class="btn btn-outline btn-sm" type="button" data-act="v-returned" data-id="${t.id}">Sudah dikembalikan</button></div>`).join('')}</section>`:''}
    </div><div class="stack">
      <section class="card panel"><div class="panel-head"><h2>Validasi BigSeller</h2>${vl?`<span class="pill ${vl.error?'p-bad':vl.masalah?'p-prep':'p-done'}">${vl.error?'gagal':vl.masalah?vl.masalah+' tidak cocok':'semua cocok'}</span>`:''}</div>
        <p class="hint">Setiap pukul ${String(V.jam).padStart(2,'0')}.00, nomor pesanan yang diserah-terimakan kemarin dicek ke BigSeller: cocok bila pesanan ada &amp; sudah diserahkan ke kurir.</p>
        ${!V.siap?`<div class="alert a-warn"><i data-lucide="plug"></i><span>Validasi belum disambungkan ke Voyage (QH_VOYAGE_INTERNAL / QH_VOYAGE_KEY).</span></div>`:''}
        ${V.log.length?`<div class="mini">${V.log.map(l=>`<div class="r"><span class="num">${esc(l.tgl)} · ${hhmm(l.selesai)}</span><b>${l.error?`<span class="late">gagal: ${esc(l.error)}</span>`:`${l.cocok}/${l.total} cocok${l.masalah?` · <span class="late">${l.masalah} tidak cocok</span>`:''}`}</b></div>`).join('')}</div>`:'<p class="hint">Belum pernah berjalan.</p>'}
        ${V.masalah.length?`<div class="tbl-wrap"><table><thead><tr><th>No. pesanan</th><th>Tiket</th><th>Hasil</th><th>Status BigSeller</th></tr></thead><tbody>${V.masalah.map(m=>`<tr><td class="num">${esc(m.no_pesanan)}</td><td class="no num">${esc(m.tiket_no)}</td><td><span class="pill ${HASIL[m.hasil]?.[1]||''}">${esc(HASIL[m.hasil]?.[0]||m.hasil)}</span></td><td>${esc(m.status||'–')}${m.kurir?`<div class="hint">${esc(m.kurir)}</div>`:''}</td></tr>`).join('')}</tbody></table></div>`:''}
        <div class="row-inline"><div class="field" style="flex:1;min-width:160px"><label for="v-tgl">Validasi ulang tanggal</label><input class="input num" id="v-tgl" type="date" value="${esc(ui.vtgl||kmr)}"></div><button class="btn btn-outline" type="button" data-act="v-validasi" ${V.jalan||!V.siap?'disabled':''}><i data-lucide="refresh-cw"></i>${V.jalan?'Sedang berjalan…':'Validasi sekarang'}</button></div>
      </section>
      <section class="card panel"><div class="panel-head"><h2>Tiket hari ini</h2><span class="hint num">${rows.length} tiket</span></div>
        <input class="input" id="sv-q" placeholder="Cari nomor antrian, nama, HP, sorter, booking, atau nomor pesanan" value="${esc(ui.q)}">
        ${rows.length?`<div class="tbl-wrap"><table><thead><tr><th>No.</th><th>Driver</th><th>Booking</th><th>Status</th><th>Sorter / Loket</th><th></th></tr></thead><tbody>
          ${rows.map(t=>`<tr><td class="no num">${esc(t.no)}</td><td>${esc(t.nama)}<div class="hint">${hhmm(t.created_at)}</div></td><td>${t.bookings.map(b=>esc(b.kode)).join(', ')}${t.pesanan.length?`<div class="hint">${t.pesanan.length} pesanan dipindai</div>`:''}</td><td>${stPill(t.status)}</td><td>${esc(t.sorter||t.siap_by||'–')}<div class="hint">${t.loket?'Loket '+esc(t.loket):''}</div></td><td>${['menunggu','disiapkan','siap'].includes(t.status)?`<button class="btn btn-quiet btn-sm" type="button" data-act="v-cancel" data-id="${t.id}">Batalkan</button>`:''}</td></tr>`).join('')}
        </tbody></table></div>` : empty('vidi-sleepy.png', qq?'Tidak ada yang cocok':'Belum ada tiket hari ini', qq?'Coba kata kunci lain.':'Tiket muncul di sini begitu driver mengirim dari barcode gudang.')}
      </section>
    </div></div>`;
}
function svAct(a, el){
  const i = Number(el.dataset.i), t = el.dataset.id && byId(el.dataset.id);
  if(a==='c-open') aksi({aksi:'loket_buka', i});
  if(a==='c-batas'){ const v=Number((document.getElementById('cfg-batas')||{}).value); aksi({aksi:'batas', menit:v}); }
  if(a==='v-cancel' && t && confirm(`Batalkan tiket ${t.no}?`)) aksi({aksi:'batal', id:t.id}, `${t.no} dibatalkan`);
  if(a==='v-returned') aksi({aksi:'kembali_rak', id:el.dataset.id});
  if(a==='v-lepas' && t) aksi({aksi:'lepas', id:t.id}, `${t.no} dikembalikan ke antrean`);
  if(a==='v-validasi'){ const tg=(document.getElementById('v-tgl')||{}).value; ui.vtgl=tg; aksi({aksi:'validasi', tgl:tg}); }
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
      <a class="btn btn-quiet btn-sm" href="${BASE}/antri/${l}" target="_blank" rel="noopener"><i data-lucide="smartphone"></i>Buka halaman driver</a></div></article>`;
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
  const tab = e.target.closest('[data-tab]'); if(tab){ e.preventDefault(); ui.tab=tab.dataset.tab; history.replaceState(null,'',BASE+'/'+ui.tab); muat().then(render); render(); window.scrollTo(0,0); return; }
  const el = e.target.closest('[data-act]'); if(!el || el.disabled) return; const a = el.dataset.act;
  if(a==='s-done'){ const t=byId(el.dataset.id); aksi({aksi:'done', id:el.dataset.id}).then(r=>{ if(r){ const x=byId(el.dataset.id); toast(x&&x.loket?`${t.no} dikirim ke Loket ${x.loket}`:`${t.no} siap, tapi belum ada loket ${LAY[t.layanan].short} yang dibuka`); } }); }
  else if(a.startsWith('l-')) loketAct(a, el);
  else if(a.startsWith('c-')||a.startsWith('v-')) svAct(a, el);
  else if(a==='b-dl') unduh(el.dataset.v);
  else if(a==='b-copy') navigator.clipboard.writeText(qrUrl(el.dataset.v)).then(()=>toast('Tautan disalin'), ()=>toast(qrUrl(el.dataset.v)));
});
document.addEventListener('keydown', e => {
  if(e.key!=='Enter') return; const id = e.target && e.target.id;
  if(id==='l-scan'){ e.preventDefault(); loketAct('l-find', e.target); }
  if(id==='l-pesanan'){ e.preventDefault(); loketAct('l-scanno', e.target); }
  if(id==='cfg-batas'){ e.preventDefault(); svAct('c-batas', e.target); }
});
document.addEventListener('input', e => { if(e.target.id==='sv-q'){ ui.q=e.target.value; clearTimeout(render._t); render._t=setTimeout(render,200); } if(e.target.id==='v-tgl') ui.vtgl=e.target.value; });
document.addEventListener('change', e => { const t=e.target; if(t.dataset.change==='c-lay') aksi({aksi:'loket_layanan', i:Number(t.dataset.i), layanan:t.value}); });

(async () => {
  const h = tabDariJalur(); if(['sorter','loket','supervisor','barcode'].includes(h)) ui.tab = h;
  await muat(); render(); await muat(); render();
  setInterval(async () => { await muat(); const a=document.activeElement; if(!(a && (a.tagName==='INPUT'||a.tagName==='SELECT'))) render(); }, 4000);
})();
})();
