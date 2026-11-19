# Print orders

Clients can order prints from their download gallery: the gallery made with the finished, full-size photos. You set
the prices, review every order, invoice whoever ordered, and once the invoice is paid you order the prints at any lab
you like with the files and order sheet Lumen Aureum prepares. The app never talks to a lab and never sends mail by
itself.

## Turning it on

Print orders are **off** until you turn them on: on a new install, and after the update to 1.4. Turn them on in
**Settings, Plugins** (Print orders, Turn on); Settings then shows **Print prices**, and the steps below set them up.

Turning print orders **off** again (Settings, Plugins, Turn off):

- If any order is still open, it asks first: "N print orders are still open. Finish them first, or turn off anyway
  (they're kept and come back when print orders are on again)."
- The print pages, the review list, the print lines on Today and on each session, and Sales tax's print choices are
  hidden. Nothing is deleted: every order, line, payment, invoice and lab cost stays exactly as it was, and comes back
  when you turn print orders on again (after a fresh check of your galleries, each gallery offers prints again as
  before).
- At the next gallery check, every gallery stops offering prints: its offer comes off your web host (`offer.bin`,
  `offer-kid.php`, and the empty `orders/index.html`), so the page no longer shows Order prints and the host takes no
  new order. If the host can't be reached, Today says so and it is tried again at each check. `orders.php` stays with
  the gallery's other scripts; with no offer it takes nothing. An old copy of a gallery on an earlier web host keeps
  its offer until that copy comes down (Take the old copies down); orders sent there are still collected.
- A client whose page was already open can still send an order until the offer comes down. Nothing a client sends is
  ever lost: orders are still collected at each gallery check and before any gallery folder is removed (the same core
  rule as picks and drafts), kept, and Today says "N print orders arrived while print orders are off". Turn print
  orders on to review them.
- Print money always stays in Finances, client statements and the accountant export, on or off.
- The ship-to rule below (reducing an address a year after its order closed) keeps running.
- An administrator can keep print orders off for the whole install with `CRM_PLUGINS_OFF=prints`; the switch can't
  turn them on then. The core part (`print_core`) still collects orders and keeps the money.

## Setting it up

1. **Settings, Print prices.** A new install starts with a SAMPLE price list (4x6 to 20x30, Lustre, at prices
   portrait studios commonly charge). Check every line and press **Save** once: until then prints can't be turned on.
   - **Your lab cost** (optional): what one print costs you. Typing a cost never changes a price, on the SAMPLE list
     or your own. **Fill prices from costs (3x)** sets each price that has a cost to the cost times your markup (3 by
     default, from 1 to 20; the button shows yours), rounded up to the whole dollar, only when you press it: it first
     lists every price it will change, and History records them. Where a cost exists the list shows your margin; a
     price below 1.5 times its cost is marked and named on Today.
   - **Hide below** and **Mark below** (pixels per inch, 150 and 200 by default): a size the photo has too few pixels
     for is not offered for that photo; one just above is offered with "May look soft at this size".
   - **Shipping**, per destination: US (48 states and DC), Alaska and Hawaii, Canada (off until you write what the page
     says about it; labs often add duties), and Pick up from the studio. Each is charged at the lab's real cost for
     that order (you type it at review), that cost plus a handling amount, or a flat amount. The page shows the line
     you write, never a figure the app works out. The settings show the range of lab shipping your last orders had.
   - **How prints arrive**: one line on the page and the invoice (by default "Prints come straight from our lab.").
2. **Offer prints on download galleries**, and which kinds of session offer them. Each gallery also has its own
   switch in its Gallery block on the client page (**Offer prints from this gallery**).
3. **Sales tax** (Settings, Sales tax): charge tax on prints (yes by default when you charge sales tax) and on
   shipping (no by default). Under a regional rate a print order is taxed where it ships (pick up: where the studio
   is). Whether to charge tax on prints and shipping, and at what rate, is your decision with your accountant;
   Lumen Aureum applies what you choose. The lab's own sales tax is your cost, not tax you collected.

Print ordering needs a web host that runs PHP 7.4 or later (Setup checks it). On a plain bucket the gallery says
"For prints, email" with your address instead.

## What the client sees

On a download gallery, each photo has **Order prints**. Sizes that fit the whole photo come first; for any other
shape, the client drags a frame over the photo to choose what prints, and can turn it. They choose how many, then
**Your print order** collects them (on that device only: no name, address or price is stored there). Then **Ship
to**: their name, email, phone (optional), where the prints go (to me, to someone else, or pick up), a note, and one
box: "I'll get an invoice. Nothing is printed until it's paid." Without the tick, the page shows "Tick 'I'll get an
invoice' to continue." beside the box. **Is this right?** shows every print as it will be cut, then **Send to** (your studio's name).

The order is sealed in their browser so only your Lumen Aureum can open it. The sent screen shows the whole order
(to print or save as a PDF) and says the invoice comes by email, usually within the days you set. Clients can't edit
a sent order in the gallery: changes and cancellations go through you.

Ordering closes 24 hours before the gallery does, so no order arrives for a gallery about to come down. The closing
notice on a gallery that offers prints adds "Order prints by" with the day and time. From then on the order button is
gone, and the gallery says ordering has closed and to email you (your address, as a link) to ask for the gallery to be
extended. A page left open past that time can't send an order either: `orders.php` refuses it. An order sent before
then is always collected, however late the app checks. Extending the gallery opens ordering again.

## Reviewing an order

Orders arrive at the regular gallery check (every 30 minutes) and before any gallery folder is removed from a host.
The orderer is matched to a client by email. Someone not on file lands on **Print orders from people not on file**
(Today): bill them as a guest, add them to the gallery's client, make a new client, or delete it as not a real order.
The app never makes a client by itself. **Make a new client** makes the orderer a client in their own right: their own
client record (the address they shipped to themselves becomes theirs on file), with this order and its payments on
their own statement, and a note on their record naming the family whose gallery they ordered from. An orderer who is
already a client of another family is matched by email, and the order is on their own statement too. An order that opens but breaks the order rules is shown as unreadable and
never acted on.

Each order's page shows:

- **Check**: anything to look at before you invoice (the email or the ship-to not on file, the first order from this gallery, a large
  order, a likely duplicate, an older price list, letters a label may not print, a PO box, Canada or Alaska and
  Hawaii, a photo changed since the order, a size that may look soft). Mark them **I have checked these**.
- **Each print as it will print**: the photo cut to the crop at the size's exact shape. Mark each cropped line
  **Looks right**, or **Change crop**. A line that may look soft needs **Print it anyway**. Until the invoice is
  issued you can change quantities and prices, or remove a line.
- **Money**: the client's prices, your lab cost and profit where known, the lab's shipping (type it) and what the
  client is charged for it, tax, and payments.

## Invoice, payment, and the lab

- **Make invoice** gives the order its own invoice in your usual run (INV-2026-0012), separate from the session's
  invoice: it never changes the session's balance. It cannot be issued while at-cost shipping has no amount. It says
  prints are made after it is paid in full, and that prints damaged, lost or defective are remade by the lab at no
  cost to the client while a crop, size or address the client chose is a new order. **Email it** opens a draft in your
  mail app with the invoice's number, what it is for, the total (prints, shipping, tax), what is paid so far and how
  to pay. It never says anything is attached; save the invoice as a PDF and attach it for their records, if you like.
- **Change the order** (before it is paid) voids the invoice so lines can change; a new invoice gets a new number.
  Shipping is then worked out again from your shipping rules, unless you typed the client's shipping charge by hand:
  a hand-set charge is kept, and the order's Check list says "Shipping set by hand: $X (rules now say $Y)" while the
  two differ. Clear the box to go back to the rule.
- **Record** payments (and refunds) on the order. Paid in full makes it **Paid, ready for the lab**; a partial payment
  never does.
- **Prepare for the lab** downloads one zip: a print-ready JPEG for each line, cut to the exact shape at full
  resolution (named `INV-2026-0012-1-8x10-Lustre-x2.jpg`), `order-sheet.pdf` and `order.txt` (ship-to, each file,
  size, finish, quantity, cropped or full frame; no prices and no client email). It streams straight to your browser:
  nothing readable is written in your data folders, and nothing goes to your web host. What you save on your own
  computer is yours to look after.
- Place the order at your lab, then **Ordered at the lab** with the lab's name and order number, and what it cost you
  (products, shipping, the lab's sales tax, other charges). **Mark shipped** (carrier, tracking; for pick up "Arrived
  at the studio"), **Tell** the client with a mail draft, **Mark delivered** (or Picked up), **Close**. A shipped order
  closes by itself 45 days after it shipped.
- **Damaged, defective or lost**: Remake requested, with a note; ask your lab for a remake under its own policy and
  type the remake's order number and what it cost you (blank: a free remake, $0). The remake's cost is recorded on
  its own; the first order's cost stays as it was.
- **Cancel order**: before ordering at the lab, at any time (record a refund separately if they paid). After ordering,
  it asks whether you canceled it at the lab too. Only "Canceled at the lab" takes the lab's cost back; otherwise the
  cost you paid stays recorded. If the lab cancels or refunds a lab order later, open the order and use **The lab
  canceled or refunded this** under its cost, with the day the money came back.
- **Lab costs are recorded as paid and never rewritten.** Each lab order, remake and lab refund is its own line on the
  day it was paid (or refunded), and stays in that year: a later remake or refund never changes a past year.
- **Order before payment** (a danger action, per order): lets you prepare and order before the invoice is paid. You
  carry the risk; History records it. Nothing automatic ever uses it.
- **Unpaid**: Today reminds you after 7 days. At 30 days the order becomes **Expired unpaid**: its invoice is voided
  (the number is never reused), the photo files stop being held for it, and a mail draft to the client is offered.
  **Reopen** it if they pay later, while the gallery's files still exist. An order whose ship-to was already reduced
  to city, state and ZIP (a year after it closed) reopens normally with a **Shipping address incomplete** box: type
  the full address there. Until you do, Prepare for the lab, Ordered at the lab and any lab plugin hand-off wait.

## What is kept, and for how long

- While a download gallery offers prints, or any order from it is open, the gallery's copy at home is kept: the usual
  deletion of the shoot's files waits, and Today says "Kept for print orders". When the hold ends, the deletion runs on
  its date, or after a fresh week's notice if that date has passed.
- Orders are financial records and stay with the session (they go when the session or client is deleted; their money
  stays in the year's figures). A session that still holds print payments can't be deleted until they are refunded or
  voided.
- Ship-to addresses: only an address a client sent prints to for someone else ("ship to someone else"), and the
  address on a guest order never accepted as a client, are reduced. Each is kept in full until one year after its
  order closed (closed, canceled or expired), then reduced to city, state and ZIP. A client's own address is never
  reduced, including an orderer you added to the family or made a client of their own. Issued invoices stay as they
  were printed.
- Finances shows print sales, shipping charged, sales tax collected on print invoices, the lab's cost and your
  profit, kept apart from your sessions. For a year with print orders, the accountant export adds `prints.csv` and
  `print-lab-costs.csv` (one line per lab cost as paid: the date paid, the lab order, and whether it was a lab order,
  a remake or a lab refund).

## Lab plugins

A plugin can connect one lab (plugins/README.md, "The lab hook"). "Where prints are made" then lists it beside "By
hand, at any lab". Core keeps the order's state, money and review rules either way; Prepare for the lab always stays
available.
