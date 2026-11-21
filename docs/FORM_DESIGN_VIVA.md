# Form design: viva notes

Rubric row: *Viva on form design: simplicity, field types, user guidance, clear layout,
explanation.* Each section gives the decision, the reason, and where to point in the
app or code. Screenshots are in `docs/screenshots/`.

## The forms in the system

| Form | Who | Fields | DFD |
| --- | --- | --- | --- |
| Log in | everyone | email, password | 1.0 |
| Create account | citizen | name, email, password, confirm | 1.0 |
| Report a problem | citizen | description, photo (optional) | 2.0 |
| Update task status | agency staff | one select: the next allowed step | 5.2 |
| Reason (reject, retry, cancel, waive) | reviewer | reason | 4.3, 5.4 |
| Add agency / task type / change SLA / create account | admin | see `app/forms.py` | 1.0 |

## 1. Simplicity: ask only for what the process needs

- **Each form asks only for what its DFD process takes as input.** The complaint form
  has two fields because DFD 2.0 takes "complaint text" and "attachment". The system
  finds the citizen (from the login), the date, the status and the agencies itself.
- **Nothing the server knows is asked again.** No citizen id, no role on registration,
  no agency on the staff form: those come from APP_USER. Asking would also let someone
  type a different value, so leaving them out is both simpler and safer.
- **Staff update is one select and one button per task row**, offering only the legal
  next steps (READY -> "Start work"; IN PROGRESS -> "Mark completed" / "Report a
  failure"). Staff never see the full status list, so they cannot pick a wrong one.
- **No dates to type.** Deadlines (`ready_at`, `due_at`) are set by the database from the
  SLA when a task becomes ready, so no one can enter a wrong deadline.
- **Optional is rare and marked.** Only the photo and descriptions are optional; they
  say "(optional)". Required fields have a `*`.

## 2. Field types: the right input for each value

| Value | Field type | Why |
| --- | --- | --- |
| Email | `<input type="email" inputmode="email">` | Phone keyboard shows `@`; browser can autofill |
| Password | `type="password"`, `autocomplete="current-password"` / `"new-password"` | Hidden text; lets password managers fill or suggest one |
| Name | text, `autocomplete="name"` | Autofill |
| Complaint description | textarea, 6 rows, max 2,000, live counter | Free text of varying length; the counter shows how much room is left |
| Photo | file, `accept=".jpg,.jpeg,.png"` | The file picker shows only images (still checked on the server) |
| Task status | select with only the allowed choices | A fixed set; a select cannot hold a typo |
| Role, agency (admin) | select | Fixed lists from the database |
| SLA hours | `type="number" min=1 max=8760` | Whole hours only; the browser offers a number keypad |
| Task code | text, upper-cased automatically, pattern hint | Codes like PIPE_REPAIR; the user does not have to hold Shift |
| Reason | textarea, 5-500 characters | Free text, required for the audit log |

Radio buttons were considered for the staff status (2-3 options) but a select keeps
each table row to one line; with radios the row would grow.

## 3. User guidance

- **A hint under each label** says what to type and the limits, before the user makes a
  mistake: "Say what you see and the exact place, e.g. *Water pipe burst outside 12th
  Main, near the bus stop.* 20 to 2,000 characters."
- **Errors in plain words that say how to fix them**, next to the field: "Use 20 to 2,000
  characters.", "The two passwords do not match.", "Agency staff must belong to an
  agency." Never a code or a stack trace. (Screenshots 02, 03, 20.)
- **Error summary at the top** ("Please fix 3 problems") with links to each field; the
  page moves focus to it so keyboard and screen-reader users hear it first.
- **The user's input is kept** after an error (except passwords), so nothing is retyped.
- **Status in words citizens understand.** The citizen sees "Waiting for another step",
  "Being worked on", "Done", not `BLOCKED` or `IN_PROGRESS` (`citizen.TASK_STATUS_TEXT`).
- **A clear message after every action**: "Complaint #102 submitted. You can follow its
  progress here.", "Task 3 is ready again with a new deadline."
- **Errors from the database are explained too**: if a reviewer tries something the
  rules forbid, the trigger's message is shown ("Not done: ...") instead of a crash.
- **Accessibility**: every input has a `<label>`; hints and errors are linked with
  `aria-describedby`; an invalid field has `aria-invalid="true"`; the counter is
  `aria-live`; the main list tables have a caption for screen readers and `scope="col"` headers.

## 4. Clear layout

- **One column, label above the field.** Easiest to scan and works on a phone without
  extra layout code.
- **Groups with headings.** The complaint form is "1. Describe the problem",
  "2. Add a photo" (`<fieldset>` + `<legend>`).
- **One main button per form**, coloured, at the end; "Cancel" next to it as a plain
  secondary button.
- **Every field is drawn by one macro** (`templates/_form.html` `field()`), so label,
  hint, input and error are always in the same order and style on every page.
- **Each role lands on its own home page** with only its own menu (`base.html`), so a
  citizen never sees reviewer links.
- **Tables for lists.** Staff tasks are sorted earliest deadline first; overdue rows get a
  red background and the word "Overdue" (not colour alone). Reviewer complaints are
  newest first.

## 5. Explanation: why the server checks everything again

The browser helps (`type="email"`, `maxlength`, `accept`, the counter) but is never
trusted. The forms carry `novalidate` so the browser's own pop-ups do not appear and all
messages come from one place, the server, in the same style. A request built by hand
(curl, a changed hidden field) is checked by the same WTForms rules, and the database
still enforces the same rule with a CHECK or trigger as the last line. See
`SECURITY_AND_VALIDATION.md`, section 5, for each rule side by side.

## Likely viva questions

**Why only two fields on the complaint form? Where is category, location, agency?**
DFD 2.0 takes only text and an attachment. Picking agencies and tasks is DFD 3.0 (NLP
proposal) and 4.x (reviewer acceptance). Asking the citizen for an agency would push a
job onto them that the system is designed to do, and they would often pick wrong.

**Why is the role not on the registration form?**
Anyone can register, so a role field would let anyone make themselves an admin. The
server always inserts CITIZEN; staff and reviewers are created by the admin. A test sends
a forged `role=ADMIN` and checks it is ignored.

**Why a select for task status instead of buttons?**
Both work; the select keeps one compact form per row and holds only legal next steps.
The server checks the step again and the database trigger checks it a third time.

**What if JavaScript is turned off?**
Everything still works. JavaScript only adds the character counter, moves focus to the
error summary, and resets the agency box when the role is not agency staff. No rule
depends on it.

**Why are error messages the same for wrong email and wrong password on login?**
Different messages would tell an attacker which emails are registered.

**Why is the photo optional?**
Many problems (no water, streetlight out at night) cannot be photographed well; a
required photo would stop people reporting.

**How do you stop a huge description or file?**
`maxlength` and the counter in the browser; `Length(20, 2000)` in the form; 5 MB request
limit with a friendly 413 page for files.

**Why a hint under the label and not placeholder text inside the box?**
Placeholder text disappears when the user starts typing and is often low contrast; a hint
stays visible while they type.
