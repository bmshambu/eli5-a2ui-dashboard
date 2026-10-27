> Copied unchanged from the [eli5-a2ui](https://github.com/bmshambu/eli5-a2ui) boilerplate. Deploy with `AGENT_MODE=probe` to re-test what a GE tenant allows before trusting the dashboard there.

# Step 0 probe: what does IFrameSrcdoc allow in GE chat?

The ELI5 explainer is an HTML page in an `IFrameSrcdoc`. Rendering with inline CSS, inside a card and inside a `Canvas`, is already proven (v0.9 concierge `guidelines.md` section 8.6). These are the open questions:

| # | question | why it matters |
|---|---|---|
| Q1 | Does JavaScript run inside the frame? | Level tabs (ELI5 to Expert) switch in the frame with no round trip. Without JS they become native `MaterialTabs` with one frame per level. |
| Q2 | Does JS work inside a `Canvas` too? | The explainer lives in the side panel. |
| Q3 | Does a `postMessage({type: 'a2ui_action'})` from the frame reach the agent? In what shape? | Follow-up chips inside the explainer ("Go deeper", related questions). Without it, follow-ups are native buttons under the frame. |
| Q4 | Does a ~200 KB page render? | A rich explainer with SVG is 30 to 100 KB; this gives headroom. |

The probe is deterministic: no model call, a callback picks the card from the exact word you type. Synthetic content only.

## Run it

Deploy and register as in the [README](README.md). Then, in GE chat, keep **F12, Console** open and type one trigger per message:

| # | type | look for |
|---|---|---|
| 0 | `help` | the trigger list renders. If not, stop. |
| 1 | `iframe-js` | green pill "JavaScript: running"; clicking ELI10, ELI15, Expert switches the text; the diagram shows |
| 2 | `iframe-canvas` | opener card in chat, side panel opens; same checks as 1 inside the panel |
| 3 | `iframe-action` | click **A**, then **B**, then **C** (native button), one at a time. Each that reaches the agent gets a **Received** card with the raw payload |
| 4 | `iframe-200kb` | renders like `iframe-js`, no error |

For row 3, copy each **Received** card's raw payload into the log below. If A and B produce nothing, note any Console line mentioning `postMessage`, `sandbox` or `a2ui_action`.

## Results log

Run 2026-10-22 in GE chat by Sharya. **All pass: planned design confirmed** (tabs and follow-up chips inside the explainer page).

| probe | result | exact observation / error |
|---|---|---|
| `help` renders | pass | card lists all five triggers; MaterialText markdown rendered as a bullet list with bold names |
| Q1 `iframe-js`: JS pill green? | pass | green "JavaScript: running" pill |
| Q1 `iframe-js`: tabs switch? | pass | Expert tab showed the Rayleigh text; inline SVG drew |
| Q2 `iframe-canvas` | pass | compact opener card in chat, side panel auto-opened, pill green, tabs live |
| Q3 button A (`parent.postMessage`) | pass | Received card, `via: "parent"` |
| Q3 button B (`window.top.postMessage`) | pass | Received card, `via: "top"` |
| Q3 button C (native `MaterialButton`) | pass | Received card, `name: probe_native_click`, `sourceComponentId: "native"` |
| Q3 raw payload shape | captured | see below |
| Q4 `iframe-200kb` | pass | rendered like `iframe-js`, no truncation |
| Console errors, if any | not captured | nothing about `sandbox` or `postMessage` reported |

The frame posts `{type: 'a2ui_action', action: 'probe_iframe_click', data: {via, prompt}}`. The agent receives:

```json
{"data": {"version": "v0.9",
          "action": {"name": "probe_iframe_click",
                     "context": {"prompt": "Clicked button parent in the iframe", "via": "parent"},
                     "sourceComponentId": "frame", "surfaceId": "probe-action-a97d2beca9f2",
                     "timestamp": "2026-10-22T10:08:20.331Z"}},
 "metadata": {"mimeType": "application/json+a2ui"}}
```

What this means for the build:

- The frame's `action` becomes `action.name` and its `data` becomes `action.context` verbatim.
- In-frame and native clicks arrive in the same envelope (only `sourceComponentId` differs), so one handler keyed on `action.name` serves both.
- GE shows `context.prompt` as a visible user turn, so a follow-up chip sends the question itself as its prompt.
- `parent` and `window.top` both work; the explainer uses `window.parent`.

## What the results mean

| outcome | design |
|---|---|
| JS runs, `postMessage` arrives | Planned design: tabs and follow-up chips inside the explainer page. |
| JS runs, `postMessage` doesn't arrive | Tabs in the page; follow-ups as native `MaterialButton`s under the frame. |
| JS blocked | Static HTML per level inside native `MaterialTabs`; follow-ups as native buttons. Diagram stays inline SVG. |
| 200 KB fails | Lower the page budget and re-test at 100 KB. |
