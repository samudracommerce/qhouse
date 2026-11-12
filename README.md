# Q-House

Antrian pengambilan paket **SPX Instant & Sameday** di gudang VitaminDiskon. Bagian dari **Wareflow** (app gudang),
milik Logistics & Infra. Dibangun dari prototipe artifact "Antrian Gudang Instant" (Sheva, 12 Nov 2026).

## Peran

| Siapa | Jalur | Login |
|---|---|---|
| Driver (orang luar) | `/antri/instant`, `/antri/sameday` (dari QR di pintu gudang) | tanpa login |
| Sorter, Loket, Supervisor, Barcode | `/` | lewat Voyage (voyage-gate) |

Peran staf dari Voyage › Akses App › Q-House (`X-Voyage-Role`): `supervisor`/`admin` = semua tab; `sorter`; `loket`;
`member` = sorter + loket; `viewer` = lihat saja. Administrator Voyage = supervisor.

Alur tiket: menunggu → disiapkan (diambil sorter) → siap (semua resi dipindai, dapat loket bergiliran per layanan) → selesai
(serah terima di loket). Cabang: bermasalah (booking tidak ditemukan → supervisor: cari ulang / keluarkan / batalkan) dan
dibatalkan (supervisor, atau otomatis lewat batas ambil → paket dikembalikan ke rak).

## Keamanan jalur publik

Hanya `/antri/instant|sameday`, `/api/publik/tiket`, berkas yang dipakai halaman driver (`driver.js`, `app.css`, ikon, gambar)
dan `/healthz` yang terbuka tanpa login — di Caddy gerbang **dan** di server (`PUBLIK_HALAMAN`, `PUBLIK_STATIC`). Driver hanya
bisa membuat tiket dan melihat tiketnya sendiri lewat token acak (HP disamarkan, tanpa resi); maks. 6 tiket / 10 menit per IP;
satu tiket aktif per nomor HP. Semua jalur lain wajib `X-Voyage-Gate` = `GATE_SECRET`.

## Jalankan

Python 3.11 standar, tanpa dependensi. Data SQLite di `QH_DATA` (default `/data`).

| Env | Isi |
|---|---|
| `GATE_SECRET` | sama dengan voyage-gate (≥ 24 karakter) |
| `QH_PUBLIC_URL` | alamat publik app, dipakai isi QR (prod: `https://qhouse.samudracommerce.com`) |
| `QH_VOYAGE_URL` | alamat Voyage |
| `QH_REQUIRE_GATE` | `1` (default) |

Docker: `Dockerfile.fleet` (port 8000, volume `/data`). Tes: `python test_qhouse.py`.
