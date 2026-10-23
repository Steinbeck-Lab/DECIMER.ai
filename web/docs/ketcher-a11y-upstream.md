# Ketcher accessibility issues: text for an upstream report (EPAM, `epam/ketcher`)

Status: draft, not posted. Posting is the owner's decision. Our workarounds are `web/src/editor/labels.ts` (items 1 and 2:
the labels; item 4: the tab order of the scroll arrows) and `web/src/editor/focusKeys.ts` (item 5: Shift+Tab); delete
the part for each item when Ketcher fixes it.

The text below is written to be pasted as one issue (or split into five: the items are independent).

---

**Title:** Standalone editor: two icon-only buttons and the clipboard textarea have no accessible name; small touch targets; keyboard focus is lost on the toolbar scroll arrow; Shift+Tab is taken by a selection-tool hotkey

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

## 4. Keyboard: Tab from the last button of a toolbar group lands on a scroll arrow that then disappears

`ArrowScroll` (`ketcher-react/dist/index.js`, `var ArrowScroll`) renders each scroll arrow as a plain `<button>`
(`data-testid="arrow-scroll-right-button"` / `"arrow-scroll-left-button"`, no `tabindex`) and renders it only while
the toolbar's end (`endInView`) or start (`startInView`) is out of view. In the DOM the arrows come right after the
toolbar's last button, so they are the next tab stop after "Add Image" (left toolbar) and "Extended Table" (right
toolbar).

Focusing the last button scrolls the toolbar into place. A quarter to a third of a second later `endInView` becomes
true and Ketcher stops rendering the right arrow. A Tab pressed inside that window focuses the arrow, the scroll then
finishes, the arrow is removed from the DOM while it has focus, and focus falls to `<body>`: the keyboard user has no
focus ring for one press. The next Tab continues from where the arrow was.

Evidence (Chromium, 1440 x 900, editor loaded):

```
focus [title="Add Image"]       12 ms   focusin images
Tab                             23 ms   focusin arrow-scroll-right-button
                               304 ms   arrow-scroll-right-button removed; document.activeElement = BODY
focus [title="Extended Table"]  10 ms   focusin extended-table
Tab                             16 ms   focusin arrow-scroll-right-button
                               245 ms   arrow-scroll-right-button removed; document.activeElement = BODY
```

A Tab pressed a second after the focus finds the arrow already gone and nothing happens. In a Tab sweep from the top of
the editor the arrows are normally removed before focus reaches the last button (we saw them go while "text" and
"period-table" had focus), so the dip needs the last button to be focused before its toolbar has scrolled (we use `.focus()`)
and the next Tab to follow within about a third of a second.

Reproduction: open the editor, `document.querySelector('[title="Add Image"]').focus()`, press Tab at once, wait 400 ms:
`document.activeElement === document.body` and the focused arrow is no longer connected. The same with
`[title="Extended Table"]` (right toolbar).

Suggested fix: keep the arrows mounted but `disabled` (or `hidden`) when there is nothing to scroll to, or take them out
of the tab order (`tabindex="-1"`): they are a pointer affordance, since Tab scrolls every button into view.

Related, seen in the same walk: when a dialog opened from a toolbar ("Extended Table") is closed, Ketcher's `Dialog`
moves focus to the hidden clipboard textarea (`clipArea.focus()` in its cleanup), not back to the button that opened
it, so the next Tab leaves the toolbar. We do not change that.

## 5. Keyboard: Shift+Tab is a hotkey, so focus cannot move backwards from a toolbar button or the clipboard field

The four selection tools are bound to `Shift+Tab` and `Escape` (`ketcher-react/dist/index.js`, the `select-rectangle`,
`select-lasso`, `select-structure` and `select-fragment` entries, `shortcut: ['Shift+Tab', 'Escape']`). The handler on
the editor's root (`keyHandle`) ignores a key only while a modal is open or when the target is a text input; for
everything else, including a toolbar button and the clipboard textarea (Ketcher keeps focus there to receive copy and
paste), `handleHotkeyGroup` ends in `event.preventDefault()`, which cancels the browser's move of focus.

Reproduction: open the editor, `document.querySelector('[data-testid="H-button"]').focus()` (a button of the right
toolbar), press Shift+Tab three times: `document.activeElement` is still the H button each time. The same on the
clipboard textarea after a click in the canvas. A keyboard user who has reached either can only go forwards, all
the way round the page (WCAG 2.1.1 Keyboard, 2.4.3 Focus Order).

Suggested fix: do not bind Shift+Tab (Escape and the toolbar buttons already reach the selection tools), or leave it to
the browser when the target is a button or the clipboard field.

## What we do meanwhile

DECIMER.ai (decimer.ai) embeds the standalone editor. From outside (a `MutationObserver` on the editor's container,
because Ketcher re-renders its toolbars) it sets `aria-label` on the three controls of items 1 and 2, and
`tabindex="-1"` on the scroll arrows of item 4 (a tabindex that Ketcher sets itself is left alone). For item 5 a
capture-phase keydown handler on the editor's container stops Shift+Tab before it reaches Ketcher and does not cancel
it, so the browser moves focus: in our editor Shift+Tab no longer cycles the selection tools, which stay on their toolbar
buttons and on Escape; every other key goes to Ketcher as before. With the labels the
page scores 97 in Lighthouse, and the remaining 3 points are item 3: `target-size` on 7 nodes with the arrows in the tab
order, on 3 nodes (all in the top toolbar) once they are out of it (same page, measured with and without the workaround). The tests are `web/src/editor/labels.test.ts`,
`web/src/editor/focusKeys.test.ts` and `web/e2e/toolbar-focus.spec.ts`; the latter fails if Ketcher stops drawing the
arrows, which is the signal to delete the workaround for item 4.
