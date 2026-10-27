"""Self-contained HTML pages for the iframe probe. Inline CSS, JS and SVG only: the
IFrameSrcdoc frame enforces connect-src 'none', so nothing may load from the network."""
import html

_STYLE = """
:root{--bg:#fff;--ink:#1f1f1f;--muted:#5f6368;--line:#dadce0;--accent:#1a73e8;--tint:#e8f0fe}
@media (prefers-color-scheme:dark){:root{--bg:#1f1f1f;--ink:#e8eaed;--muted:#9aa0a6;--line:#3c4043;--accent:#8ab4f8;--tint:#283142}}
*{box-sizing:border-box}body{margin:0;padding:16px;font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink)}
h1{font-size:20px;margin:0 0 4px}.muted{color:var(--muted);font-size:13px}
.status{display:inline-block;margin:8px 0 12px;padding:2px 10px;border-radius:12px;font-size:13px;background:#fce8e6;color:#a50e0e}
.status.ok{background:#e6f4ea;color:#137333}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:12px}
.tabs button{border:0;background:none;padding:8px 12px;font:inherit;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent}
.tabs button[aria-selected=true]{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.panel{display:none}.panel.on{display:block}
svg{width:100%;height:auto;max-width:560px;display:block;margin:12px 0}
.btn{border:1px solid var(--accent);background:var(--tint);color:var(--ink);border-radius:18px;padding:8px 16px;font:inherit;cursor:pointer;margin:4px 8px 4px 0}
pre{white-space:pre-wrap;word-break:break-all;font-size:12px;background:var(--tint);padding:12px;border-radius:8px}
"""

_LEVELS = [
    ("eli5", "ELI5", "Sunlight is made of all the colours mixed together. When it bumps into the tiny bits of air in the sky, the blue part bounces around the most, so blue comes at your eyes from every direction."),
    ("eli10", "ELI10", "White sunlight is a mix of colours, each a different wavelength. Air molecules scatter short wavelengths (blue) much more than long ones (red). That scattered blue light reaches us from all over the sky."),
    ("eli15", "ELI15", "This is Rayleigh scattering: molecules much smaller than the light's wavelength scatter it with a strength proportional to 1 over wavelength to the fourth power. Blue (about 450 nm) scatters roughly 4 times more than red (about 700 nm). Violet scatters even more, but sunlight has less of it and our eyes are less sensitive to it."),
    ("expert", "Expert", "Rayleigh scattering cross-section scales as λ⁻⁴ for particles with size parameter x = 2πr/λ ≪ 1. Integrated over the solar spectrum, atmospheric optical depth and the CIE photopic response, the diffuse skylight peaks perceptually in the blue, while the direct beam is reddened at low solar elevation."),
]

_DIAGRAM = """
<svg viewBox="0 0 560 160" role="img" aria-label="Sunlight scatters off air molecules; blue reaches the eye from all directions">
  <defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="currentColor"/></marker></defs>
  <g fill="none" stroke="currentColor" stroke-width="1.5" color="#9aa0a6">
    <circle cx="60" cy="70" r="30" fill="#fbbc04" stroke="#f29900"/>
    <path d="M95 70H240" marker-end="url(#a)"/>
  </g>
  <g fill="#1a73e8"><circle cx="270" cy="70" r="7"/><circle cx="300" cy="50" r="5"/><circle cx="290" cy="95" r="6"/></g>
  <g fill="none" stroke="#1a73e8" stroke-width="1.5"><path d="M280 62L330 25" marker-end="url(#a)"/><path d="M285 80L340 120" marker-end="url(#a)"/><path d="M305 60L440 70" marker-end="url(#a)"/></g>
  <g fill="none" stroke="currentColor" color="#9aa0a6" stroke-width="1.5"><ellipse cx="475" cy="70" rx="22" ry="14"/><circle cx="475" cy="70" r="6" fill="currentColor"/></g>
  <g font-size="13" fill="currentColor" color="#5f6368" font-family="system-ui,sans-serif">
    <text x="60" y="122" text-anchor="middle">Sunlight</text><text x="290" y="140" text-anchor="middle">Air molecules</text><text x="475" y="110" text-anchor="middle">Your eye</text>
    <text x="395" y="100" text-anchor="middle">blue scatters most</text>
  </g>
</svg>"""

_TABS_JS = """
<script>
document.getElementById('js').textContent='JavaScript: running';
document.getElementById('js').className='status ok';
document.querySelectorAll('.tabs button').forEach(function(b){
  b.addEventListener('click',function(){
    document.querySelectorAll('.tabs button').forEach(function(x){x.setAttribute('aria-selected',x===b)});
    document.querySelectorAll('.panel').forEach(function(p){p.classList.toggle('on',p.id===b.dataset.panel)});
  });
});
</script>"""


def _doc(body: str) -> str:
    return f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{_STYLE}</style></head><body>{body}</body></html>'


def js_page(padding_bytes: int = 0) -> str:
    """Level tabs switched by JavaScript plus an inline SVG diagram. The status pill turns green
    only if scripts run; with JS blocked it stays red and the ELI5 panel stays visible."""
    tabs = "".join(
        f'<button aria-selected="{"true" if i == 0 else "false"}" data-panel="{key}">{label}</button>'
        for i, (key, label, _) in enumerate(_LEVELS)
    )
    panels = "".join(
        f'<div class="panel{" on" if i == 0 else ""}" id="{key}"><p>{html.escape(text)}</p></div>'
        for i, (key, _, text) in enumerate(_LEVELS)
    )
    # padding is an HTML comment: invisible, but it makes the payload realistic in size
    padding = f"<!-- {'x' * padding_bytes} -->" if padding_bytes else ""
    return _doc(
        '<h1>Why is the sky blue?</h1><div class="muted">Probe: JavaScript tabs + inline SVG</div>'
        '<span id="js" class="status">JavaScript: blocked</span>'
        f'<div class="tabs" role="tablist">{tabs}</div>{panels}{_DIAGRAM}{padding}{_TABS_JS}'
    )


def action_page() -> str:
    """Buttons that post an a2ui_action to the host, the way Google's GE sample does it."""
    return _doc(
        '<h1>Click back to the agent</h1>'
        '<div class="muted">Each button calls postMessage. If GE forwards it, the agent replies with a "Received" card.</div>'
        '<span id="js" class="status">JavaScript: blocked</span><div>'
        '<button class="btn" onclick="send(\'parent\')">A: parent.postMessage</button>'
        '<button class="btn" onclick="send(\'top\')">B: window.top.postMessage</button>'
        '</div><p id="sent" class="muted"></p>'
        "<script>"
        "document.getElementById('js').textContent='JavaScript: running';document.getElementById('js').className='status ok';"
        "function send(via){"
        "var msg={type:'a2ui_action',action:'probe_iframe_click',data:{via:via,prompt:'Clicked button '+via+' in the iframe'}};"
        "(via==='top'?window.top:window.parent).postMessage(msg,'*');"
        "document.getElementById('sent').textContent='Sent via '+via+' at '+new Date().toLocaleTimeString();}"
        "</script>"
    )


def raw_page(title: str, raw: str) -> str:
    """Shows raw incoming JSON escaped, since MaterialText swallows < and >."""
    return _doc(f"<h1>{html.escape(title)}</h1><pre>{html.escape(raw)}</pre>")
