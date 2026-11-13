"""The sample wording that shipped in the 1.0 release candidates. An install that had reviewed it keeps exactly these words as
its own when it upgrades (init: _freeze_reviewed_wording), so a release never changes a contract its photographer
approved. Never edit this file."""

STANDARD_RC = {
    "agreement": """# 1. Who this is between
This agreement is between [CLIENT] ("you") and [PHOTOGRAPHER] ("I" or "me").

# 2. Booking and payment
The deposit holds your date and is applied to the price. The balance is [BALANCE DUE]. [PAYMENT] Location permits, parking and venue fees are charged at what they cost me and are never discounted.

# 3. Canceling and rescheduling
[CANCELLATION]

# 4. If I cannot do the session
If I cannot photograph your session because of illness, injury, emergency or equipment failure, I will offer a new date first. If no new date works for you, I refund everything you have paid. That refund is the full extent of my responsibility.

# 5. What I provide, and what you receive
[WHAT I PROVIDE] You receive the edited photos [HOW YOU DELIVER], within [DELIVERY DAYS] days of the session. I keep the unedited files for [HOW LONG] after delivery, then delete them.

# 6. The photos
[THE PHOTOS]

# 7. How I may use the photos
[HOW I MAY USE THEM]

# 8. Minors
[MINORS]

# 9. Products in my care
[PRODUCTS]

# 10. Safety and locations
You are responsible for your own safety and belongings at each location. Some locations may refuse photography on the day, and we will move to another spot.

# 11. The whole agreement
This agreement and the quote are our whole agreement. Changes are made in writing. The law of [LAW] applies.
""",
    "transfer": """# The photos are yours
[PHOTOGRAPHER] transfers to you all copyright in the photos delivered from this session, effective when the session is paid in full. You may print, share, publish, sell or change them however you like, with no permission needed.

# Portfolio
[FAVORITES]

# Printing
If a print lab asks whether you may print these photos, show them this page: they belong to you.
""",
    "license": """# Your license
[PHOTOGRAPHER] keeps the copyright. Your license to the photos delivered from this session covers [WHAT THE LICENSE COVERS].[LICENSE SCOPE]

# Other uses
Anyone else who wants to use the photos needs their own license from me.

# Portfolio
[PORTFOLIO]
""",
    "release": """# Permission to photograph
I give [PHOTOGRAPHER] permission to photograph [PERSON] at [OCCASION].

# How the photos are used
[EVENT RIGHTS]

[HOW THE PHOTOS ARE USED]

# Parent or guardian
[GUARDIAN]
""",
}


# The portfolio paragraph of the pre-1.0 transfer letter. [FAVORITES] became the whole portfolio promise in 1.0 (it
# matches the agreement), so a frozen copy of this letter has the old paragraph replaced by [FAVORITES] (app.py
# _freeze_reviewed_wording repairs copies frozen before this fix).
OLD_TRANSFER_PORTFOLIO = 'You give me permanent permission to show [FAVORITES] in my portfolio and on my website. It continues after this transfer and cannot be withdrawn. If a serious reason arises later, you may ask me to take a photo down; I will consider each request individually and may grant an exception at my discretion. I show no other photo from your session anywhere.'
