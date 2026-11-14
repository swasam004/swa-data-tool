"""Look and feel of the web interface (CSS, icons, page frame). No business logic here."""
from __future__ import annotations

from html import escape

SITE = "https://swasamson.site"
SITE_UTM = SITE + "/?utm_source=swa-data-tool&utm_medium=app&utm_campaign=powered-by"

ICON = {
    "logo": "<svg viewBox='0 0 32 32' aria-hidden='true'><defs><linearGradient id='lg' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#22d3ee'/><stop offset='1' stop-color='#7c4dff'/></linearGradient></defs><rect width='32' height='32' rx='9' fill='url(#lg)'/><rect x='7' y='16' width='4.5' height='9' rx='1.5' fill='#fff'/><rect x='13.7' y='10' width='4.5' height='15' rx='1.5' fill='#fff'/><rect x='20.5' y='6' width='4.5' height='19' rx='1.5' fill='#fff' opacity='.9'/></svg>",
    "plus": "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M12 5v14M5 12h14' stroke='currentColor' stroke-width='2.4' stroke-linecap='round' fill='none'/></svg>",
    "arrow": "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M5 12h14M13 6l6 6-6 6' stroke='currentColor' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round' fill='none'/></svg>",
    "upload": "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M7 18a4 4 0 0 1-.6-7.96A6 6 0 0 1 18 9.5a4.2 4.2 0 0 1-.5 8.5H15M12 20v-8m0 0-3 3m3-3 3 3' stroke='currentColor' stroke-width='1.9' stroke-linecap='round' stroke-linejoin='round' fill='none'/></svg>",
    "check2": "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M3 12.5l4 4L13 9M10 16.5l1 1L21 7' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' fill='none'/></svg>",
    "shield": "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z' stroke='currentColor' stroke-width='1.9' stroke-linejoin='round' fill='none'/><path d='M9 12l2 2 4-4' stroke='currentColor' stroke-width='1.9' stroke-linecap='round' stroke-linejoin='round' fill='none'/></svg>",
    "kobo": "<svg viewBox='0 0 24 24' aria-hidden='true'><rect x='4' y='3' width='16' height='18' rx='3' stroke='currentColor' stroke-width='1.9' fill='none'/><path d='M8 8h8M8 12h8M8 16h5' stroke='currentColor' stroke-width='1.9' stroke-linecap='round'/></svg>",
    "sheet": "<svg viewBox='0 0 24 24' aria-hidden='true'><rect x='3' y='4' width='18' height='16' rx='2.5' stroke='currentColor' stroke-width='1.9' fill='none'/><path d='M3 9.5h18M9 4v16M15 4v16' stroke='currentColor' stroke-width='1.6'/></svg>",
    "ext": "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' fill='none'/></svg>",
    "download": "<svg viewBox='0 0 24 24' aria-hidden='true'><path d='M12 4v11m0 0-4-4m4 4 4-4M5 20h14' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' fill='none'/></svg>",
}

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E"
           "%3Cstop offset='0' stop-color='%2322d3ee'/%3E%3Cstop offset='1' stop-color='%237c4dff'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='32' height='32' rx='9' fill='url(%23g)'/%3E"
           "%3Crect x='7' y='16' width='4.5' height='9' rx='1.5' fill='white'/%3E%3Crect x='13.7' y='10' width='4.5' height='15' rx='1.5' fill='white'/%3E%3Crect x='20.5' y='6' width='4.5' height='19' rx='1.5' fill='white'/%3E%3C/svg%3E")

CSS = r"""
@font-face{font-family:"Figtree";src:url(/static/figtree.woff2) format("woff2");font-weight:300 900;font-style:normal;font-display:swap}
:root{--ink:#050b1d;--navy:#0a1733;--blue:#3b7bff;--cyan:#22d3ee;--teal:#14b8a6;--violet:#7c4dff;--amber:#ffb84d;--rose:#ff6b8b;
--txt:#eaf0ff;--mut:#9db0d6;--line:rgba(255,255,255,.14);--glass:rgba(255,255,255,.06);--ok:#34d399;--warn:#fbbf24;--bad:#fb7185;
--font:"Figtree",ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
*{box-sizing:border-box}html{scroll-behavior:smooth;overflow-x:hidden;overflow-x:clip;max-width:100%}
body{margin:0;font:400 17px/1.55 var(--font);color:var(--txt);background:var(--ink);min-height:100vh;overflow-x:hidden;overflow-x:clip;max-width:100%;position:relative;-webkit-font-smoothing:antialiased}
a{color:var(--cyan)}img,svg{max-width:100%}
.bg{position:fixed;inset:0;z-index:-2;overflow:hidden;background:
radial-gradient(1100px 620px at 12% -8%,rgba(59,123,255,.42),transparent 62%),
radial-gradient(900px 520px at 92% 6%,rgba(124,77,255,.34),transparent 62%),
radial-gradient(1000px 640px at 50% 112%,rgba(20,184,166,.30),transparent 62%),
linear-gradient(180deg,#07112b,#050b1d)}
.bg::after{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px);background-size:52px 52px;-webkit-mask-image:radial-gradient(ellipse at 50% 18%,#000 25%,transparent 78%);mask-image:radial-gradient(ellipse at 50% 18%,#000 25%,transparent 78%)}
.orb{position:absolute;z-index:-1;border-radius:50%;filter:blur(70px);opacity:.5;pointer-events:none;animation:drift 18s ease-in-out infinite alternate}
.orb.a{width:380px;height:380px;background:#2f6bff;left:-90px;top:30vh}.orb.b{width:320px;height:320px;background:#14b8a6;right:-70px;top:60vh;animation-delay:-6s}
@keyframes drift{to{transform:translate3d(40px,-60px,0) scale(1.12)}}
.skip{position:absolute;left:-999px}.skip:focus{left:12px;top:12px;background:#fff;color:#000;padding:8px 12px;border-radius:8px;z-index:99}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
header.top{position:sticky;top:0;z-index:20;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);background:rgba(5,11,29,.62);border-bottom:1px solid var(--line)}
.bar{max-width:1180px;margin:0 auto;padding:12px 22px;display:flex;min-width:0;align-items:center;gap:22px}
.brand{display:flex;align-items:center;gap:10px;color:#fff;text-decoration:none;font-weight:800;font-size:19px;letter-spacing:-.01em;margin-right:auto;min-width:0}
.brand svg{width:34px;height:34px}.brand small{font-weight:600;color:var(--mut);font-size:13px;margin-left:2px}
nav.main{display:flex;align-items:center;gap:6px}
nav.main a{color:var(--mut);text-decoration:none;font-weight:600;font-size:15px;padding:8px 13px;border-radius:99px;transition:.2s}
nav.main a:hover,nav.main a.on{color:#fff;background:var(--glass)}
.pill{display:inline-flex;align-items:center;gap:7px;color:#fff!important;background:linear-gradient(135deg,rgba(34,211,238,.22),rgba(124,77,255,.28));border:1px solid rgba(255,255,255,.22);padding:8px 14px!important;border-radius:99px;font-weight:700!important}
.pill svg{width:14px;height:14px}.pill:hover{transform:translateY(-1px);box-shadow:0 8px 22px rgba(80,90,255,.35)}
main{max-width:1180px;margin:0 auto;padding:0 22px}
.hero{text-align:center;padding:64px 0 10px}
.eyebrow{display:inline-flex;align-items:center;gap:8px;font-weight:700;font-size:14px;color:#c9d8ff;border:1px solid var(--line);background:var(--glass);padding:7px 16px;border-radius:99px;letter-spacing:.01em}
.eyebrow i{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 0 rgba(52,211,153,.7);animation:pulse 2s infinite}
@keyframes pulse{70%{box-shadow:0 0 0 10px rgba(52,211,153,0)}100%{box-shadow:0 0 0 0 rgba(52,211,153,0)}}
h1.big{font-size:clamp(2.5rem,6.4vw,4.7rem);line-height:1.04;letter-spacing:-.03em;font-weight:800;margin:22px auto 18px;max-width:900px}
.grad{background:linear-gradient(100deg,#22d3ee 0%,#6aa0ff 40%,#b18cff 75%,#ff9bd0 100%);-webkit-background-clip:text;background-clip:text;color:transparent;background-size:200% 100%;animation:sheen 8s ease-in-out infinite alternate}
@keyframes sheen{to{background-position:100% 0}}
.lead{font-size:clamp(1.05rem,2vw,1.28rem);color:#c3d1f2;max-width:720px;margin:0 auto 26px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:9px;border:0;cursor:pointer;font:700 16px var(--font);color:#fff;text-decoration:none;padding:14px 26px;border-radius:99px;background:linear-gradient(135deg,#7c4dff,#4f6bff);box-shadow:0 10px 28px rgba(110,80,255,.45);transition:transform .2s,box-shadow .2s;position:relative;overflow:hidden}
.btn:hover{transform:translateY(-2px);box-shadow:0 16px 36px rgba(110,80,255,.55)}
.btn::after{content:"";position:absolute;top:0;left:-70%;width:50%;height:100%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.35),transparent);transform:skewX(-20deg);animation:shine 4.5s infinite}
@keyframes shine{60%,100%{left:130%}}
.btn svg{width:20px;height:20px}.btn.ghost{background:var(--glass);border:1px solid var(--line);box-shadow:none}.btn.ghost::after{display:none}
.btn.sm{padding:10px 18px;font-size:15px}.btn:disabled{opacity:.45;cursor:not-allowed;transform:none}
.trust{display:flex;flex-wrap:wrap;gap:8px 22px;justify-content:center;margin-top:20px;color:var(--mut);font-size:14.5px}
.trust span{display:inline-flex;align-items:center;gap:7px}.trust svg{width:18px;height:18px;color:var(--ok)}
.stage{position:relative;margin:46px auto 0;border-radius:34px;padding:clamp(26px,5.5vw,76px) clamp(14px,4vw,60px) clamp(34px,6vw,84px);overflow:hidden;isolation:isolate;
background:linear-gradient(135deg,#1d5bff 0%,#6a4dff 42%,#16b9a8 100%);background-size:220% 220%;animation:shift 16s ease infinite;box-shadow:0 40px 90px rgba(25,60,210,.38),inset 0 1px 0 rgba(255,255,255,.35)}
.stage::before{content:"";position:absolute;inset:0;z-index:-1;background:radial-gradient(circle at 1px 1px,rgba(255,255,255,.22) 1.2px,transparent 1.4px) 0 0/26px 26px;opacity:.55;-webkit-mask-image:linear-gradient(180deg,#000,transparent 85%);mask-image:linear-gradient(180deg,#000,transparent 85%)}
.stage::after{content:"";position:absolute;left:0;right:0;bottom:0;height:46%;z-index:-1;background:linear-gradient(180deg,transparent,rgba(255,255,255,.2))}
@keyframes shift{50%{background-position:100% 100%}}
.chip{position:absolute;z-index:0;width:150px;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.35);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);color:#fff;border-radius:16px;padding:12px 14px;font-weight:700;font-size:13px;box-shadow:0 14px 34px rgba(5,10,60,.3);animation:float 7s ease-in-out infinite alternate}
.chip small{display:block;font-weight:600;opacity:.8;font-size:11.5px}.chip b{font-size:22px;letter-spacing:-.02em}
.chip.c1{left:3.5%;top:9%}.chip.c2{right:4%;top:15%;animation-delay:-2s}.chip.c3{left:5%;bottom:11%;animation-delay:-4s}.chip.c4{right:5%;bottom:9%;animation-delay:-1s}
.bars{display:flex;align-items:flex-end;gap:5px;height:38px;margin-top:6px}.bars i{width:9px;border-radius:3px 3px 0 0;background:#fff;opacity:.9;animation:grow 2.6s ease-in-out infinite alternate}
.bars i:nth-child(1){height:40%}.bars i:nth-child(2){height:75%;animation-delay:.3s}.bars i:nth-child(3){height:55%;animation-delay:.6s}.bars i:nth-child(4){height:95%;animation-delay:.9s}.bars i:nth-child(5){height:70%;animation-delay:1.2s}
@keyframes grow{to{transform:scaleY(.62)}}@keyframes float{to{transform:translateY(-16px) rotate(1.2deg)}}
.drop{position:relative;z-index:2;max-width:760px;margin:0 auto;min-height:300px;background:#fff;color:#0b1330;border:3px solid #7c4dff;border-radius:30px;padding:30px 32px 26px;display:flex;flex-direction:column;box-shadow:0 30px 70px rgba(6,12,70,.4);transition:transform .25s,box-shadow .25s,border-color .25s;cursor:pointer}
.drop:hover{transform:translateY(-3px);box-shadow:0 38px 80px rgba(6,12,70,.48)}
.drop.over{border-color:var(--amber);transform:scale(1.015);box-shadow:0 0 0 8px rgba(255,184,77,.35),0 38px 80px rgba(6,12,70,.5)}
.drop:focus-within{outline:3px solid rgba(124,77,255,.45);outline-offset:4px}
.prompt{font-size:clamp(1.25rem,3vw,1.85rem);font-weight:600;letter-spacing:-.02em;color:#1b2446;min-height:2.6em;display:flex;align-items:flex-start;gap:6px;text-align:left}
.prompt span{color:#8d96b5}.caret{display:inline-block;width:3px;height:1.25em;background:#ff8a1f;border-radius:2px;margin-top:.2em;animation:blink 1.1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
.file{display:none;align-items:center;gap:12px;background:#f2f0ff;border:1.5px solid #d9d2ff;border-radius:16px;padding:12px 16px;text-align:left}
.file.on{display:flex}.file svg{width:30px;height:30px;color:#6a4dff;flex:none}.file b{display:block;word-break:break-all;line-height:1.25}.file small{color:#6c7597}
.title-in{margin-top:12px;border:0;border-bottom:2px solid #e7e9f4;background:transparent;font:600 16px var(--font);color:#1b2446;padding:10px 2px;width:100%;outline:0;transition:border-color .2s;cursor:text}
.title-in:focus{border-color:#7c4dff}.title-in:focus-visible{outline:0}.title-in::placeholder{color:#9aa3c2;font-weight:500}
.row{margin-top:auto;padding-top:22px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.add{width:54px;height:54px;border-radius:50%;border:2px solid #cfd5ea;color:#1b2446;background:#fff;display:grid;place-items:center;cursor:pointer;transition:.2s;flex:none}
.add:hover{border-color:#7c4dff;color:#7c4dff;transform:rotate(90deg)}.add svg{width:26px;height:26px}
.sr-only:focus-visible+.add{outline:3px solid #7c4dff;outline-offset:3px}
.fmt{display:flex;gap:7px;flex-wrap:wrap;color:#6c7597;font-size:13.5px;font-weight:600;align-items:center;margin-right:auto}
.fmt b{background:#eef1fb;color:#34406b;border-radius:99px;padding:3px 11px;font-weight:700;font-size:12.5px}
select.prof{border:1.5px solid #d6dbee;border-radius:99px;padding:9px 14px;font:600 14px var(--font);color:#1b2446;background:#fff;max-width:220px;cursor:pointer}
.go{width:62px;height:62px;border-radius:50%;border:0;color:#fff;display:grid;place-items:center;cursor:pointer;background:linear-gradient(135deg,#7c4dff,#5b6bff);box-shadow:0 12px 26px rgba(110,80,255,.5);transition:.2s;flex:none}
.go:hover:not(:disabled){transform:scale(1.08)}.go:disabled{background:#c9cfe6;box-shadow:none;cursor:not-allowed}.go svg{width:28px;height:28px}
.note{margin:14px auto 0;color:rgba(255,255,255,.92);font-size:14.5px;text-align:center;position:relative;z-index:2}
.err{color:#d6284a;font-weight:700;margin-top:8px;text-align:left;min-height:1.2em;font-size:14.5px}
details.cols{max-width:760px;margin:18px auto 0;color:rgba(255,255,255,.92);position:relative;z-index:2;font-size:14.5px}
details.cols summary{cursor:pointer;font-weight:700;text-align:center;list-style:none}details.cols summary::after{content:' \25BE'}details.cols[open] summary::after{content:' \25B4'}details.cols summary::-webkit-details-marker{display:none}
details.cols p{margin:10px 0 0;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.3);border-radius:14px;padding:12px 16px}
.sec{padding:76px 0 6px}.sec h2{font-size:clamp(1.7rem,3.6vw,2.5rem);letter-spacing:-.025em;line-height:1.12;margin:0 0 10px;font-weight:800;text-align:center}
.sec .sub{color:var(--mut);text-align:center;max-width:680px;margin:0 auto 34px}
.grid4{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}
.card{background:var(--glass);border:1px solid var(--line);border-radius:22px;padding:24px;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);transition:transform .25s,border-color .25s,background .25s}
.card:hover{transform:translateY(-5px);border-color:rgba(130,170,255,.55);background:rgba(255,255,255,.085)}
.ico{width:48px;height:48px;border-radius:14px;display:grid;place-items:center;margin-bottom:14px;color:#fff}.ico svg{width:26px;height:26px}
.i1{background:linear-gradient(135deg,#3b7bff,#22d3ee)}.i2{background:linear-gradient(135deg,#7c4dff,#ff6b8b)}.i3{background:linear-gradient(135deg,#14b8a6,#34d399)}.i4{background:linear-gradient(135deg,#ffb84d,#ff6b8b)}
.card h3{margin:0 0 6px;font-size:19px;letter-spacing:-.01em}.card p{margin:0;color:var(--mut);font-size:15.5px}
.steps{counter-reset:s;display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:18px}
.step{position:relative;padding-top:58px}.step::before{counter-increment:s;content:counter(s);position:absolute;left:24px;top:-18px;width:46px;height:46px;border-radius:50%;display:grid;place-items:center;font-weight:800;font-size:20px;color:#fff;background:linear-gradient(135deg,#22d3ee,#7c4dff);box-shadow:0 10px 24px rgba(60,100,255,.5)}
.step{margin-top:18px;padding-top:34px}
.promo{margin:80px 0 0;border-radius:30px;padding:clamp(26px,4vw,48px);display:flex;gap:26px;align-items:center;flex-wrap:wrap;background:linear-gradient(120deg,rgba(34,211,238,.18),rgba(124,77,255,.26) 55%,rgba(255,107,139,.2));border:1px solid rgba(255,255,255,.22)}
.promo h3{margin:0 0 6px;font-size:clamp(1.4rem,3vw,2rem);letter-spacing:-.02em}.promo p{margin:0;color:#cbd8f8;max-width:560px}.promo>div{flex:1;min-width:260px}
footer.foot{margin-top:70px;border-top:1px solid var(--line);background:rgba(3,7,20,.6)}
.foot .in{max-width:1180px;margin:0 auto;padding:30px 22px 40px;display:flex;flex-wrap:wrap;gap:16px 34px;align-items:center;color:var(--mut);font-size:14.5px}
.foot a{color:#c8d6ff;text-decoration:none;font-weight:700}.foot a:hover{color:#fff;text-decoration:underline}.foot .sp{margin-left:auto}
.prose{max-width:780px;margin:0 auto}.prose h2{font-size:24px;letter-spacing:-.02em;margin:34px 0 8px}.prose p,.prose li{color:#cbd8f6;font-size:17px}.prose ul{padding-left:22px}.prose code{background:rgba(255,255,255,.1);padding:1px 6px;border-radius:6px}
.ad{max-width:min(var(--w,728px),100%);margin:26px auto;text-align:center}
.ad small{display:block;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#7f93bd;margin-bottom:5px}
.ad img{border-radius:14px;display:block;margin:0 auto;max-width:100%;height:auto}.adframe{border:0;border-radius:12px;background:#fff;display:block;margin:0 auto;max-width:100%}
.ad-top{margin:8px auto 0}.ad-top small{display:none}.ad-top img{max-height:60px;width:auto;border-radius:10px}.ad-top .adframe{max-height:60px}
.ad-top .tad{flex-direction:row;align-items:center;gap:10px;padding:8px 14px;border-radius:12px;overflow:hidden}.ad-top .tad b{font-size:14px;white-space:nowrap}.ad-top .tad span{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1}.ad-top .tad em{font-size:12.5px;white-space:nowrap}
.advlink{color:var(--mut)!important;font-weight:600!important;font-size:13.5px}
.tad{display:flex;flex-direction:column;gap:4px;text-align:left;padding:18px 22px;border-radius:16px;background:linear-gradient(120deg,rgba(255,184,77,.16),rgba(124,77,255,.2));border:1px solid rgba(255,255,255,.22);color:#fff;text-decoration:none;transition:.2s}
.tad:hover{transform:translateY(-2px);border-color:#fff}.tad b{font-size:18px}.tad span{color:#d3def8;font-size:15px}.tad em{font-style:normal;font-weight:800;color:var(--amber);font-size:14px}
.pagehead{padding:46px 0 18px;text-align:center}.pagehead h1{font-size:clamp(2rem,5vw,3.1rem);letter-spacing:-.03em;margin:12px 0 8px;font-weight:800;line-height:1.08}.pagehead p{color:var(--mut);margin:0}
.status{display:inline-flex;align-items:center;gap:8px;font-weight:800;padding:8px 18px;border-radius:99px;border:1.5px solid currentColor;font-size:15px;background:rgba(0,0,0,.18)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:26px 0}
.stat{background:var(--glass);border:1px solid var(--line);border-radius:20px;padding:18px 20px;text-align:left;position:relative;overflow:hidden}
.stat::after{content:"";position:absolute;left:0;bottom:0;height:4px;width:100%;background:linear-gradient(90deg,var(--cyan),var(--violet))}
.stat b{display:block;font-size:clamp(1.9rem,4vw,2.6rem);letter-spacing:-.03em;line-height:1.1}.stat span{color:var(--mut);font-size:14.5px;font-weight:600}
.panel{background:var(--glass);border:1px solid var(--line);border-radius:26px;padding:clamp(20px,3vw,32px);margin:18px 0;backdrop-filter:blur(10px)}
.panel h2{margin:0 0 10px;font-size:20px;letter-spacing:-.01em}.panel ul{margin:8px 0 0;padding-left:20px;color:#cdd9f6}.panel li{margin:5px 0}
.dl{display:flex;flex-wrap:wrap;gap:12px;margin-top:6px}.dl .btn svg{width:18px;height:18px}
.msgbad{border:1.5px solid var(--bad);background:rgba(251,113,133,.12);border-radius:16px;padding:14px 18px;margin:12px 0}
.panel.prose{margin:18px auto}
.reportbox{max-width:100%;background:#fff;border-radius:22px;overflow:hidden;border:1px solid var(--line);box-shadow:0 24px 60px rgba(0,0,0,.4)}
.reportbox iframe{display:block;width:100%;height:78vh;border:0;background:#fff}
.keep{color:var(--mut);font-size:14px;text-align:center;margin:10px 0 0}
table.runs{width:100%;border-collapse:collapse}.runs td,.runs th{text-align:left;padding:13px 10px;border-bottom:1px solid var(--line);font-size:15.5px}.runs a{color:#fff;font-weight:700;text-decoration:none}.runs a:hover{color:var(--cyan)}
textarea.code{width:100%;height:58vh;font:13.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:rgba(2,6,20,.7);color:#d9e6ff;border:1px solid var(--line);border-radius:16px;padding:16px;resize:vertical}
.banner{border:1.5px solid var(--warn);color:#ffe29a;background:rgba(251,191,36,.1);border-radius:16px;padding:12px 18px;margin:18px 0 0;text-align:left;font-size:15px}
.banner a{color:#fff;font-weight:800}
.overlay{position:fixed;inset:0;z-index:100;background:rgba(4,9,26,.86);backdrop-filter:blur(8px);display:none;flex-direction:column;align-items:center;justify-content:center;gap:22px;text-align:center;padding:24px}
.overlay.on{display:flex}.loader{display:flex;gap:8px;align-items:flex-end;height:64px}.loader i{width:14px;border-radius:5px 5px 0 0;background:linear-gradient(180deg,#22d3ee,#7c4dff);animation:bounce 1.1s ease-in-out infinite}
.loader i:nth-child(2){animation-delay:.15s}.loader i:nth-child(3){animation-delay:.3s}.loader i:nth-child(4){animation-delay:.45s}.loader i:nth-child(5){animation-delay:.6s}
@keyframes bounce{0%,100%{height:18px}50%{height:64px}}.overlay h3{margin:0;font-size:24px;letter-spacing:-.02em}.overlay p{margin:0;color:var(--mut);max-width:420px}
.js .reveal{opacity:0;transform:translateY(22px);transition:opacity .7s ease,transform .7s ease}.js .reveal.in{opacity:1;transform:none}
:focus-visible{outline:3px solid rgba(34,211,238,.7);outline-offset:3px}
@media(max-width:1180px){.chip{display:none}}
@media(max-width:860px){main{padding:0 14px}.foot .in{padding:24px 14px 32px}.foot .sp{margin-left:0}.hero{overflow-wrap:anywhere}.ad-top .tad span{display:none}nav.main a:not(.pill){display:none}.bar{padding:10px 14px;gap:10px}.brand{font-size:17px}.brand span{white-space:nowrap}.pill{padding:7px 12px!important;font-size:13.5px}.drop{padding:22px 18px}.go{width:56px;height:56px;order:3;margin-left:auto}.add{order:1}select.prof{order:2;flex:1;min-width:0;max-width:none}.fmt{order:4;flex:1 1 100%;margin:0}.hero{padding-top:40px}.stage{border-radius:26px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}.js .reveal{opacity:1;transform:none}html{scroll-behavior:auto}}
"""

JS = r"""
document.documentElement.classList.add('js');
(function(){
 var io=('IntersectionObserver' in window)?new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target)}})},{threshold:.12}):null;
 document.querySelectorAll('.reveal').forEach(function(el){io?io.observe(el):el.classList.add('in')});
 var rm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
 document.querySelectorAll('[data-count]').forEach(function(el){
  var end=parseFloat(el.getAttribute('data-count'));if(isNaN(end)||rm){return}
  var t0=null,dur=900;function step(t){if(!t0)t0=t;var p=Math.min((t-t0)/dur,1),e=1-Math.pow(1-p,3);
   el.textContent=Math.round(end*e).toLocaleString();if(p<1)requestAnimationFrame(step)}
  el.textContent='0';requestAnimationFrame(step)});
 var f=document.getElementById('file');if(!f)return;
 var drop=document.getElementById('drop'),go=document.getElementById('go'),chip=document.getElementById('chip'),
     nm=document.getElementById('fname'),sz=document.getElementById('fsize'),err=document.getElementById('err'),
     prompt=document.getElementById('prompt'),max=parseInt(drop.getAttribute('data-max'),10)||52428800,
     ok=/\.(csv|tsv|txt|xlsx|xlsm|xls|json)$/i;
 function human(n){return n<1048576?(n/1024).toFixed(0)+' KB':(n/1048576).toFixed(1)+' MB'}
 function show(){var x=f.files[0];err.textContent='';
  if(!x){go.disabled=true;chip.classList.remove('on');prompt.style.display='';return}
  if(!ok.test(x.name)){err.textContent='That file type is not supported. Please choose a CSV, Excel or JSON file.';f.value='';go.disabled=true;return}
  if(x.size>max){err.textContent='That file is too large ('+human(x.size)+'). The limit is '+human(max)+'.';f.value='';go.disabled=true;return}
  nm.textContent=x.name;sz.textContent=human(x.size)+' - ready to analyse';chip.classList.add('on');prompt.style.display='none';go.disabled=false}
 f.addEventListener('change',show);
 drop.addEventListener('click',function(e){if(e.target.closest('button,select,input,label,a'))return;f.click()});
 ['dragenter','dragover'].forEach(function(ev){drop.addEventListener(ev,function(e){e.preventDefault();drop.classList.add('over')})});
 ['dragleave','drop'].forEach(function(ev){drop.addEventListener(ev,function(e){e.preventDefault();drop.classList.remove('over')})});
 drop.addEventListener('drop',function(e){if(e.dataTransfer&&e.dataTransfer.files.length){f.files=e.dataTransfer.files;show()}});
 var cta=document.getElementById('cta');if(cta)cta.addEventListener('click',function(e){e.preventDefault();drop.scrollIntoView({behavior:rm?'auto':'smooth',block:'center'});setTimeout(function(){f.click()},rm?0:450)});
 var tips=['Rejected rows are listed with the reason, so nothing disappears silently.','Every figure is calculated twice by independent methods.','Personal data columns can be hashed or masked in all outputs.','Your uploaded file is deleted as soon as the analysis ends.'],ti=0,tp=document.getElementById('tip');
 document.getElementById('f').addEventListener('submit',function(){document.getElementById('overlay').classList.add('on');go.disabled=true;if(tp)setInterval(function(){ti=(ti+1)%tips.length;tp.textContent=tips[ti]},2600)});
})();
"""


def frame(title: str, body: str, *, active: str = "", show_admin: bool = False, top_ad: str = "", footer_ad: str = "",
          retention_hours: int = 0, noindex: bool = False, description: str = "", advertise: str = "", canonical: str = "", jsonld: str = "",
          gsc: str = "") -> str:
    desc = description or "Turn raw data into verified insights: pivot tables, charts, indicators and integrity checks from CSV, Excel or JSON files."
    nav_items = [("/", "Analyse", "home"), ("/#how", "How it works", "how"), ("/history", "Past runs", "history")]
    if show_admin:
        nav_items.append(("/admin", "Admin", "admin"))
    nav = "".join(f"<a href='{h}'{' class=on' if active == k else ''}>{t}</a>" for h, t, k in nav_items)
    keep = f"Results are deleted automatically after {retention_hours} hours." if retention_hours else "Results stay on this server until the owner deletes them."
    robots = "<meta name='robots' content='noindex,nofollow'>" if noindex else "<meta name='robots' content='index,follow,max-image-preview:large'>"
    seo = (f"<link rel='canonical' href='{escape(canonical, quote=True)}'><meta property='og:url' content='{escape(canonical, quote=True)}'>" if canonical and not noindex else "")
    seo += (f"<meta name='google-site-verification' content='{escape(gsc, quote=True)}'>" if gsc else "")
    seo += (f"<script type='application/ld+json'>{jsonld}</script>" if jsonld and not noindex else "")
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{escape(title)} | SWA Data Analysis</title><meta name='description' content='{escape(desc, quote=True)}'>{robots}
{seo}<meta name='theme-color' content='#050b1d'><meta property='og:title' content='{escape(title, quote=True)} | SWA Data Analysis'><meta property='og:site_name' content='SWA Data Analysis'><meta name='twitter:card' content='summary'>
<meta property='og:description' content='{escape(desc, quote=True)}'><meta property='og:type' content='website'>
<link rel='icon' href="{FAVICON}"><style>{CSS}</style></head><body>
<a class='skip' href='#content'>Skip to content</a><div class='bg'><div class='orb a'></div><div class='orb b'></div></div>
<header class='top'><div class='bar'><a class='brand' href='/'>{ICON['logo']}<span>SWA Data Analysis</span></a>
<nav class='main'>{nav}<a class='pill' href='{SITE_UTM}' target='_blank' rel='noopener'>swasamson.site {ICON['ext']}</a></nav></div></header>
{top_ad}<main id='content'>{body}</main>
{footer_ad}
<footer class='foot'><div class='in'><span>&copy; SWA Data Analysis &middot; a tool by <a href='{SITE_UTM}' target='_blank' rel='noopener'>swasamson.site</a></span>
<span>{escape(keep)} Your uploaded file is deleted right after analysis.</span>
<span class='sp'><a href='/about'>About</a> &nbsp;&middot;&nbsp; <a href='/privacy'>Privacy</a> &nbsp;&middot;&nbsp; {advertise}{' &nbsp;&middot;&nbsp; ' if advertise else ''}<a href='{SITE_UTM}' target='_blank' rel='noopener'>swasamson.site</a></span></div></footer>
<script>{JS}</script></body></html>"""
