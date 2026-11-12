/* Q-House — halaman DRIVER (publik, tanpa akun). Dibuka dari QR di pintu gudang: /antri/instant atau /antri/sameday.
   Tiket disimpan di perangkat lewat token acak; halaman memperbarui status tiap 5 detik. */
(function(){
'use strict';
const LAY = {instant:{label:'SPX Instant', short:'Instant', cls:'p-instant', sub:'termasuk Instant Prioritas'},
             sameday:{label:'SPX Sameday', short:'Sameday', cls:'p-sameday', sub:'pengiriman hari ini'}};
const BK = {menunggu:'menunggu dipindai', dipindai:'dipindai sorter', tidak_ditemukan:'belum ditemukan', dikeluarkan:'dikeluarkan'};
const KEY = 'qhouse.tiket';
const m = location.pathname.match(/\/antri\/(instant|sameday)/);
const lock = m ? m[1] : null;
const ui = {layanan: lock || 'instant', codes: [], err: '', busy: false, t: null};

const esc = s => String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const normCode = s => String(s||'').toUpperCase().replace(/[^A-Z0-9]/g,'').slice(0,4);
const hhmm = ms => { if(!ms) return '–'; const d=new Date(ms); return String(d.getHours()).padStart(2,'0')+'.'+String(d.getMinutes()).padStart(2,'0'); };
const layPill = l => `<span class="pill ${LAY[l]?.cls||''}">${esc(LAY[l]?.label||l)}</span>`;
function lsGet(){ try{ return localStorage.getItem(KEY); }catch(e){ return null; } }
function lsSet(v){ try{ if(v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY); }catch(e){} }
function toast(msg){ const el=document.getElementById('toast'); el.textContent=msg; el.hidden=false; clearTimeout(toast._t); toast._t=setTimeout(()=>{el.hidden=true;},3200); }

async function api(method, url, body){
  const r = await fetch(url, {method, headers: body?{'Content-Type':'application/json'}:{}, body: body?JSON.stringify(body):undefined});
  let j = {}; try{ j = await r.json(); }catch(e){}
  if(!r.ok) throw Object.assign(new Error(j.error || 'Gagal, coba lagi.'), {status: r.status});
  return j;
}

function form(){
  const ok = ui.codes.length > 0;
  return `<div class="ph-head"><div><p class="eyebrow">Gudang VitaminDiskon</p><h2>Tiket antrian pengambilan</h2></div></div>
  <p class="hint">Isi datamu, lalu tambahkan semua nomor booking yang kamu ambil sekarang.</p>
  ${lock ? `<div class="field"><label>Layanan</label>
    <div class="lockbox"><span><b>${LAY[lock].label}</b><br><span class="hint">Dari barcode ${LAY[lock].short} di gudang</span></span>${layPill(lock)}</div>
    <span class="hint">Salah layanan? Scan barcode ${LAY[lock==='instant'?'sameday':'instant'].short} di pintu gudang.</span></div>`
  : `<div class="field"><label>Layanan</label><div class="seg" role="group" aria-label="Layanan">
      ${['instant','sameday'].map(l=>`<button type="button" data-act="lay" data-v="${l}" aria-pressed="${ui.layanan===l}">${LAY[l].label}<small>${LAY[l].sub}</small></button>`).join('')}</div></div>`}
  <div class="field"><label for="d-nama">Nama</label><input class="input" id="d-nama" autocomplete="name" maxlength="60" placeholder="Nama sesuai aplikasi driver"></div>
  <div class="field"><label for="d-hp">Nomor HP</label><input class="input num" id="d-hp" inputmode="tel" autocomplete="tel" maxlength="16" placeholder="08xx xxxx xxxx"></div>
  <div class="field"><label for="d-plat">Plat nomor <span class="hint">(opsional)</span></label><input class="input" id="d-plat" maxlength="16" placeholder="B 1234 XYZ"></div>
  <div class="field"><label for="d-kode">Nomor booking</label>
    <div class="addrow"><input class="input code-input" id="d-kode" maxlength="4" autocomplete="off" autocapitalize="characters" placeholder="941G"><button class="btn btn-outline" type="button" data-act="add"><i data-lucide="plus"></i>Tambah</button></div>
    <span class="hint">4 karakter dari aplikasi Shopee driver. Ulangi untuk setiap pesanan.</span></div>
  ${ui.codes.length?`<div class="codes">${ui.codes.map((c,i)=>`<div class="row"><span class="k">${esc(c)}</span><span class="s">ditambahkan</span><button class="icon-x" type="button" data-act="del" data-i="${i}" aria-label="Hapus ${esc(c)}"><i data-lucide="x"></i></button></div>`).join('')}</div>`:''}
  ${ui.err?`<p class="err">${esc(ui.err)}</p>`:''}
  <button class="btn btn-primary btn-block" type="button" data-act="kirim" ${ok&&!ui.busy?'':'disabled'}><i data-lucide="send"></i>Kirim tiket${ok?` (${ui.codes.length} nomor booking)`:''}</button>`;
}

function steps(t){
  const s = t.status, wait = ['menunggu','disiapkan','bermasalah'].includes(s), ready = s==='siap', done = s==='selesai', batal = s==='dibatalkan';
  const step = (cls, title, desc) => `<div class="step ${cls}"><div class="mk">${cls.includes('done')?'<i data-lucide="check"></i>':''}</div><div class="${cls.includes('now')?'box':''}"><div class="t">${title}</div>${desc?`<div class="d">${desc}</div>`:''}</div></div>`;
  let wd = '';
  if(s==='menunggu') wd = t.posisi ? `Posisi <b class="num">${t.posisi}</b> dari ${t.antrian} tiket` : '';
  else if(s==='disiapkan') wd = 'Petugas sedang menyiapkan paketmu';
  else if(s==='bermasalah') wd = 'Tertahan: temui petugas';
  return `<div class="steps">
    ${step('done','Tiket dibuat', `Pukul ${hhmm(t.createdAt)} · ${t.bookings.length} nomor booking`)}
    ${step(wait ? (s==='bermasalah'?'bad':'now') : (batal && !t.readyAt ? 'later' : 'done'), 'Menunggu antrian', wd)}
    ${step(ready?'now':(done||(batal&&t.readyAt)?'done':'later'), 'Barang siap diambil', ready ? (t.loket ? `<span class="loket-big">Loket ${esc(t.loket)}</span><br>${LAY[t.layanan].short} · ambil dalam <b class="num">${t.sisaMenit}</b> menit` : 'Menunggu loket dibuka. Tunggu panggilan petugas.') : (t.readyAt?`Pukul ${hhmm(t.readyAt)} · Loket ${esc(t.loket||'–')}`:''))}
    ${step(done?'done':'later', 'Selesai', done?`Pukul ${hhmm(t.doneAt)}`:'')}
  </div>`;
}

function tiket(t){
  if(t.status==='selesai') return `<div class="done-art"><img src="/static/assets/vidi-thankyou.png" alt="Vidi, maskot VitaminDiskon, berterima kasih" width="150" height="171">
      <p class="eyebrow">Tiket ${esc(t.no)}</p><h2>Paket sudah diserahkan</h2><p class="hint">Diserahkan di Loket ${esc(t.loket)} pukul ${hhmm(t.doneAt)}. Terima kasih, hati-hati di jalan.</p></div>
      ${steps(t)}<button class="btn btn-outline btn-block" type="button" data-act="baru"><i data-lucide="plus"></i>Buat tiket baru</button>`;
  let al = '';
  if(t.status==='bermasalah') al = `<div class="alert a-bad"><i data-lucide="alert-triangle"></i><span>Ada nomor booking yang paketnya belum ditemukan. Temui petugas gudang dan tunjukkan nomor tiket ini.</span></div>`;
  if(t.status==='dibatalkan') al = `<div class="alert a-warn"><i data-lucide="circle-slash"></i><span>Tiket ini dibatalkan${t.cancelReason?` (${esc(t.cancelReason)})`:''}. Buat tiket baru bila masih ada paket yang harus diambil.</span></div>`;
  return `<div class="ph-head"><div><p class="eyebrow">Tiket antrian</p><div class="ticket-no num">${esc(t.no)}</div></div>${layPill(t.layanan)}</div>
    <p class="hint">${esc(t.nama)} · ${esc(t.hp)}</p>${al}${steps(t)}
    <div class="field"><label>Nomor booking di tiket ini</label><div class="codes">${t.bookings.map(b=>`<div class="row"><span class="k">${esc(b.kode)}</span><span class="s">${esc(BK[b.status]||'')}</span></div>`).join('')}</div></div>
    <p class="hint">Halaman ini diperbarui otomatis. Tidak perlu bertanya ke petugas.</p>
    ${t.status==='dibatalkan'?`<button class="btn btn-outline btn-block" type="button" data-act="baru"><i data-lucide="plus"></i>Buat tiket baru</button>`:''}`;
}

function render(){
  const v = document.getElementById('view');
  const keep = {}; v.querySelectorAll('input[id]').forEach(el => keep[el.id] = el.value);
  const act = document.activeElement && document.activeElement.id;
  v.innerHTML = ui.t ? tiket(ui.t) : form();
  Object.keys(keep).forEach(id => { const el = document.getElementById(id); if(el) el.value = keep[id]; });
  if(act){ const el = document.getElementById(act); if(el) el.focus({preventScroll:true}); }
  if(window.lucide) window.lucide.createIcons({attrs:{'stroke-width':2}});
}

function tambah(){
  const inp = document.getElementById('d-kode'); const c = normCode(inp && inp.value); ui.err = '';
  if(c.length!==4){ ui.err = 'Nomor booking harus 4 karakter, misalnya 941G.'; render(); return; }
  if(ui.codes.includes(c)){ ui.err = `${c} sudah ada di daftar.`; render(); return; }
  ui.codes.push(c); if(inp) inp.value=''; render(); const n=document.getElementById('d-kode'); if(n) n.focus();
}

async function kirim(){
  const val = id => (document.getElementById(id)||{}).value || '';
  const sisa = normCode(val('d-kode')); if(sisa.length===4 && !ui.codes.includes(sisa)) ui.codes.push(sisa);
  ui.err=''; ui.busy=true; render();
  try{
    const r = await api('POST', '/api/publik/tiket', {layanan: lock || ui.layanan, nama: val('d-nama'), hp: val('d-hp'), plat: val('d-plat'), kode: ui.codes});
    lsSet(r.token); ui.codes=[]; toast(r.ada ? `Tiket aktifmu ${r.no} dibuka lagi` : `Tiket ${r.no} dibuat`); await muat();
  }catch(e){ ui.err = e.message; }
  ui.busy=false; render();
}

async function muat(){
  const tok = lsGet(); if(!tok){ ui.t=null; return; }
  try{ ui.t = await api('GET', '/api/publik/tiket?t=' + encodeURIComponent(tok)); }
  catch(e){ if(e.status===404){ lsSet(null); ui.t=null; } }
}

document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]'); if(!el || el.disabled) return; const a = el.dataset.act;
  if(a==='lay'){ ui.layanan = el.dataset.v; render(); }
  else if(a==='add') tambah();
  else if(a==='del'){ ui.codes.splice(Number(el.dataset.i),1); render(); }
  else if(a==='kirim') kirim();
  else if(a==='baru'){ lsSet(null); ui.t=null; render(); }
});
document.addEventListener('keydown', e => { if(e.key==='Enter' && e.target.id==='d-kode'){ e.preventDefault(); tambah(); } });
document.addEventListener('input', e => { if(e.target.id==='d-kode'){ const v=normCode(e.target.value); if(v!==e.target.value) e.target.value=v; } });

(async () => { await muat(); render(); setInterval(async () => { if(lsGet()){ await muat(); const a=document.activeElement; if(!(a && a.tagName==='INPUT')) render(); } }, 5000); })();
})();
