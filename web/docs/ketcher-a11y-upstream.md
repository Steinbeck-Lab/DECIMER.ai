# Ketcher accessibility issues: text for an upstream report (EPAM, `epam/ketcher`)

Status: draft, not posted. Posting is the owner's decision. Our workaround is `web/src/editor/labels.ts`; delete it
when Ketcher fixes items 1 and 2, and the focus tolerance mentioned in item 4.

The text below is written to be pasted as one issue (or split into four: the items are independent).

---

**Title:** Standalone editor: two icon-only buttons and the clipboard textarea have no accessible name; small touch targets; keyboard focus is lost on the toolbar scroll arrow

**Version:** `ketcher-react` / `ketcher-core` / `ketcher-standalone` 3.18.0, `<Editor>` with `disableMacromoleculesEditor`, Chromium from Playwright 1.64.1, desktop viewport 1440 x 900.

**How it was found:** Lighthouse 13.5.0, accessibility category, snapshot mode on a page with the editor loaded and a structure on the canvas. The rest of the page scores 100; with the editor loaded the score drops to 87 and every failing node is inside `.Ketcher-root`.

## 1. Two icon-only buttons have no accessible name (axe `button-name`, WCAG 4.1.2)

The small dropdown triangles beside the Copy and Aromatize buttons in the top toolbar:

```html
<button class="MuiButtonBase-root MuiIconButton-root MuiIconButton-sizeMedium expanded css-1rtpl5t"
        tabindex="0" type="button" data-testid="copy-button-dropdown-triangle"><svg ...></svg></button>
```

They have no text, no `aria-label` and no `title`, so a screen reader announces "button". Both carry the test id
`copy-button-dropdown-triangle`, although the second one belongs to the Aromatize button (its previous sibling is
`<button title="Aromatize (Alt+A)" data-testid="Aromatize button">`), which looks like a copy and paste slip.

Suggested fix: an `aria-label` (for example "More copy options" and "More aromatize options"), and `aria-haspopup`
and `aria-expanded` for the menu they open.

## 2. The clipboard textarea has no label (axe `label`, WCAG 1.3.1 / 4.1.2)

```html
<textarea class="cliparea cliparea-module_cliparea__GdPrN" data-cliparea="true" contenteditable="true"></textarea>
```

It is a form field that can take focus (Ketcher focuses it to receive copy and paste), and it has no label.

Suggested fix: `aria-label="Clipboard"` (or `aria-hidden` plus `tabindex="-1"` if it never needs to be reached by
assistive technology, which is not the case while it holds focus).

## 3. Targets smaller than 24 x 24 CSS px (axe `target-size`, WCAG 2.5.8, level AA)

Lighthouse reports 7 nodes at 1440 x 900, 100 % zoom:

| Control | Size | Count |
|---|---|---|
| The two dropdown triangles of item 1 | 9 x 9 | 2 |
| `[data-testid="arrow-scroll-left-button"]`, `[data-testid="arrow-scroll-right-button"]` | 36 x 16 | 2 |
| One 28 x 28 toolbar button each in the top toolbar (next to a triangle), the left toolbar and the right toolbar | 28 x 28 | 3 |

The 28 x 28 buttons are large enough, so they are reported for the spacing rule (the 24 px circle centred on an
undersized target must not overlap another target), most likely because of undersized neighbours. Making the triangles and the scroll arrows at least 24 x 24 (or spacing them out) should clear all seven.

## 4. Keyboard: Tab from the last button of a toolbar group lands on a control that then disappears

`ArrowScroll` renders the forward scroll arrow only while the end of the toolbar is out of view. Pressing Tab
from the last visible button of a toolbar group (we see it after "Add Image" and after "Extended Table") focuses
the arrow (`data-testid="arrow-scroll-right-button"`). Focus scrolls the toolbar until `endInView` becomes true,
Ketcher then stops rendering the arrow, the focused element is removed from the DOM, and focus falls back to
`<body>`. The next Tab continues from the top of the document (or, in our page, from the start of the dialog).

Reproduction: open the editor, focus "Add Image" (`[title="Add Image"]`), press Tab, wait 300 ms,
`document.activeElement === document.body` is true and the element that had focus is no longer connected.

Suggested fix: keep the arrow mounted but `disabled`/`aria-hidden` and `tabindex="-1"` when there is nothing to scroll
to, or move focus to the next toolbar button before removing it. Scroll arrows are also a mouse affordance; keyboard
users can already reach every button by Tab, which scrolls it into view, so the arrows could be taken out of the
tab order (`tabindex="-1"`).

## What we do meanwhile

DECIMER.ai (decimer.ai) embeds the standalone editor. It sets `aria-label` on the three controls of items 1 and 2
from outside (a `MutationObserver` on the editor's container, because Ketcher re-renders its toolbars). With that
the page scores 97 in Lighthouse; the remaining 3 points are item 3.
