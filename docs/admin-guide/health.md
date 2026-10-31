# Admin guide: the Health pages

This guide is for the people who **manage the Routine app** from the admin panel
(the "back office"). You don't need any technical knowledge. It explains, step by
step, what each Health page is for, what you can do on it, and what you can't do
(and why).

> **The one rule to remember:** most Health pages are **read-only on purpose**.
> They show what users did in the app (their plans, weights, meals). You look at
> them to help users or check that everything works, but you don't change them:
> the app keeps them correct automatically. The only Health page you edit is
> **Foods**.

---

## Contents

1. [Before you start: opening the admin panel](#1-before-you-start-opening-the-admin-panel)
2. [Moving around: lists, search, filters and dates](#2-moving-around-lists-search-filters-and-dates)
3. [Plans](#3-plans)
4. [Weight](#4-weight)
5. [Foods](#5-foods) (the page you edit)
6. [Meals](#6-meals)
7. [Metric trackables](#7-metric-trackables)
8. [Common situations: what to do when…](#8-common-situations-what-to-do-when)
9. [Words you will see](#9-words-you-will-see)

---

## 1. Before you start: opening the admin panel

1. Open the admin address in your web browser (Chrome, Safari, Edge…). Your
   developer gives you the address; it ends with **`/admin/`**
   (for example `https://api.your-domain.com/admin/`).
2. Type your **email** and **password**, then click **Log in**.
   - Forgot your password? Ask the person who created your account to set a new one.
3. You arrive on the home page. On the **left** you see the **sidebar** with groups:
   *Accounts*, *Tracking*, **Health**, *AI*, *Security*.
4. Under **Health** you find five pages: **Plans**, **Weight**, **Foods**,
   **Meals**, **Metric trackables**. Click a name to open the page.

**I don't see the Health group or some of its pages.** You only see the pages
your account is allowed to see. Ask an administrator to give your account the
right role (see *Accounts → Roles*).

**Changing the language.** Click your name (or the user icon) in the sidebar,
then choose **English**, **العربية** or **Français**. The admin switches language.
(Food names typed by people stay as they were typed.)

**Logging out.** Click your name in the sidebar, then **Log out**. Always log out
on a shared computer.

---

## 2. Moving around: lists, search, filters and dates

Every page opens on a **list** (a table). These tools work the same way on all pages:

| Tool | Where | What it does |
|------|-------|--------------|
| **Search box** | Top of the list | Type a few letters and press **Enter**. On Health pages you can usually search by the **user's email**; on Foods, by the food's **name**. |
| **Filters** | The filter button / panel next to the list | Narrow the list, for example only *breakfast* meals, or only *unverified* foods. Click a choice to apply it; choose **All** to remove it. |
| **Dates** | A row of years / months / days above the list (on Plans, Weight and Meals) | Click a year, then a month, then a day, to see only that period. |
| **Column titles** | The table header | Click a title to sort by it (click again to reverse). |
| **A row** | The first column of each row is a link | Click it to open the **details** of that item. |
| **Pages** | Bottom of the list | When there are many rows, use the page numbers to see more. |

**Opening the details.** Click the first column of a row. On read-only pages
you can see every field, but there is no **Save** button; that is normal.

**History.** On the details page, the **History** button shows who changed the
item and when (for pages you can edit, such as Foods).

---

## 3. Plans

**What it is.** Each user sets up their **plan** in the app (the "خطتك وسعراتك"
screen): their goal (lose weight, keep, gain), sex, height, age, activity, and
the app calculates their daily targets: **calories, protein, carbs, fat, water**,
plus steps (8000), activity (30 min) and sleep (8 h).

**Every change is kept.** When a user changes their plan, the app does **not**
overwrite the old one: it adds a **new row** starting that day. This way, past
days keep showing the targets that were valid then. If a user changes their plan
twice on the same day, that day's row is simply updated.

**What you see in the list:**

| Column | Meaning |
|--------|---------|
| User | The user's email |
| Effective from | The day this version of the plan started (in the user's own time zone) |
| Goal | cut (lose weight), maintain, bulk (gain) |
| Mode | *Calculated* (the app computed the targets) or *Manual* (the user typed their calories and protein) |
| Calories, Protein g, Carbs g, Fat g | Daily targets |
| Water ml | Daily water target in millilitres (1000 ml = 1 litre) |

**How to use it, step by step:**

1. Click **Health → Plans**.
2. To find one user's plans, type their **email** in the search box and press **Enter**.
3. The newest version is at the **top**. The one with the latest *Effective from*
   date (today or before) is the plan the user is following now.
4. Click a row to see all the details: birth year (the app stores the year, not
   the age), height, activity level, and whether the **calorie floor** was applied.
5. Use the filters (**Goal**, **Mode**, **Sex**) to answer questions such as "how
   many plans are weight-loss plans?" (the number of results is shown with the list).
   Remember that one user can have several versions.

**Good to know:**
- **Calorie floor applied = Yes** means the formula gave a very low number, so the
  app raised it to the safe minimum (1500 kcal for men, 1200 for women) and showed
  the user a message. This is a safety feature, not an error.
- Users must be **18 or older** to create a plan.
- **BMI** is not on this page: the app calculates it on the fly from the latest
  weight and the height.

**You cannot** add, change or delete plans here. Only the user can change their
plan, from the app. *Why?* The plan drives the user's targets and history; a change
here would silently change what the user sees for past days.

---

## 4. Weight

**What it is.** Every weight the user records: one value **per day**. Confirming a
plan also records that day's weight.

| Column | Meaning |
|--------|---------|
| User | The user's email |
| Date | The day of the weight (user's time zone) |
| Weight kg | The weight in kilograms |

**How to use it, step by step:**

1. Click **Health → Weight**.
2. Search the user's **email** and press **Enter**.
3. The most recent weight is at the top. Use the **dates** row to look at one month.

**Good to know:** if the user logs a weight twice on the same day, the second value
**replaces** the first (one weight per day). Users can record a weight for today or
up to 30 days back, never in the future.

**You cannot** edit weights here; the user can correct them in the app.

---

## 5. Foods

This is the **food database** the app searches when users log a meal ("أكلك
النهاردة", "+ سجّل يدوي") and the list the AI uses to recognise foods. It is the
**only Health page you edit**.

### 5.1 Two kinds of foods

| Kind | Who creates it | Who sees it | Can you edit it? |
|------|----------------|-------------|------------------|
| **Global food** (no user) | You (staff), or a file import done by your developer | **Every** user | **Yes** |
| **Custom food** (has a user) | A user, in the app ("my mom's cake"), or the AI when it didn't recognise a food and the user confirmed it (source *AI estimate*) | **Only that user** | **No**, read-only. It is the user's private food. |

To see one kind only: open the filters and choose **owner → Global** or
**owner → Users' custom foods**.

### 5.2 Reading the list

| Column | Meaning |
|--------|---------|
| Name (first column) | The English name (or Arabic if there's no English name) |
| Name ar | The Arabic name |
| Category | Dish, drink, fruit, bread… |
| Kcal, Protein g, Carbs g, Fat g | **Per 100 grams** of the food |
| Source | Where the numbers come from (see 5.6) |
| Is verified | ✔ = checked against a trusted source. Test foods and users' foods are not verified. |
| Is active | ✔ = users can find it. ✘ = hidden from the app. |
| User | Empty for global foods; the owner's email for custom foods |

Useful filters: **Category**, **Source**, **Is verified** (find foods that still
need checking), **Is active** (find hidden foods).

### 5.3 Find a food

1. Click **Health → Foods**.
2. Type part of the name in the search box (Arabic, English or French), for
   example `كشري` or `koshari`, and press **Enter**.

> The search box here matches the letters you type. The **app** is smarter: it also
> understands spelling variants (طعمية / طعميه, أرز / ارز) and other names
> ("aliases"). So if a user can't find a food in the app, check the spellings and
> aliases of that food here.

### 5.4 Add a new food, step by step

1. Click **Health → Foods**, then the **Add food** button (top right).
2. Fill in the form:

   | Field | What to type | Example |
   |-------|--------------|---------|
   | **Name ar** | The Arabic name, as people write it | كشري |
   | **Name en** | The English name | Koshari |
   | **Name fr** | The French name | Koshari |
   | **Aliases** | Other names people use, in any language (see below) | `["كشرى", "kushari"]` |
   | **Category** | Choose from the list | Dish |
   | **Kcal** | Calories **per 100 g** | 150 |
   | **Protein g** | Grams of protein **per 100 g** | 5 |
   | **Carbs g** | Grams of carbohydrates **per 100 g** | 27 |
   | **Fat g** | Grams of fat **per 100 g** | 2.5 |
   | **Source** | Where the numbers come from (see 5.6) | NNI (Egypt) |
   | **Key** | A short code that identifies the food: lowercase English letters, numbers and dashes, **no spaces**, unique for this source | `koshari` |
   | **Is verified** | Tick it only if you checked the numbers against a trusted source | ✔ |
   | **Is active** | Leave ticked so users can find it | ✔ |

   - At least **one name** (Arabic, English or French) is required. Fill in all three when you can.
   - All numbers are **per 100 grams**, never per plate. The app does the maths for each portion.
   - Kcal can be 0–900; protein, carbs and fat each 0–100, and **together** at most 100 (100 g of food can't contain more than 100 g of nutrients).

3. **Aliases: how to type them.** Write the names between square brackets, each
   name between double quotes, separated by commas:
   - One alias: `["فلافل"]`
   - Several: `["فلافل", "ta'meya", "taameya"]`
   - None: `[]`

   Use straight double quotes `"`, not curly ones. If you see an error on this
   field, check the brackets and quotes.

4. **Add the portions** (section *Food portions* at the bottom). A portion is a
   common serving with its weight, so users can tap "1 plate" instead of typing grams:
   1. Click **Add another Food portion**.
   2. Choose the **Unit** (plate, small plate, bowl, piece, sandwich, slice, loaf,
      cup, glass, can, bottle, tablespoon, handful).
   3. Type the **Grams** of one such portion. Example: plate → `350`, small plate → `250`.
   4. Repeat for each portion. Each unit can appear only **once** per food.

   No portion fits? Leave the section empty: users can always type grams.

5. Click **Save** (or **Save and add another** to continue with the next food).
6. If something is wrong, the page shows the problem **in red** next to the field.
   Fix it and click **Save** again.

### 5.5 Edit a food

1. Find the food (5.3) and click its name.
2. Change what you need: names, aliases, numbers, portions.
   - To **remove a portion**, tick its **Delete** box, then save.
3. Click **Save**.

**What happens to past meals?** Nothing. Every logged meal keeps a **copy** of the
numbers it had when it was logged. If you correct koshari from 150 to 160 kcal, the
meals already logged keep 150; new meals use 160. This is on purpose: history never
changes.

**Who changed it?** Click **History** on the food to see every change, who made it and when.

### 5.6 Sources

| Source | Meaning |
|--------|---------|
| NNI (Egypt) | Egyptian National Nutrition Institute tables |
| USDA | United States Department of Agriculture database |
| Open Food Facts | Packaged products (barcode database) |
| Custom | Entered by hand (by staff for a global food, or by a user for their private food) |
| AI estimate | A user's private food created from an AI estimate the user confirmed. Not verified. |

Test foods (keys starting with `test-`) were loaded for development with
approximate values; they are marked **not verified**. Replace or hide them once the
real food list is imported.

### 5.7 Hide a food, or delete it

- **Hide (recommended):** open the food, untick **Is active**, click **Save**. Users
  no longer find it; past meals are unaffected; you can show it again later by ticking it back.
- **Delete:** open the food and click **Delete**, then confirm with **Yes, I'm sure**.
  Past meals still keep their copy of the numbers, but the food is gone for good.
  Prefer hiding.

### 5.8 Large food lists

Adding hundreds of foods one by one is slow. Your developer can load a whole
spreadsheet (CSV file) at once with the **import** tool. Running the same file
again **updates** foods instead of creating duplicates, and it never re-shows foods
you hid. Ask your developer; the file format is described in
[docs/health.md → Food CSV format](../health.md#food-csv-format).

### 5.9 Several foods at once

In the list, tick the boxes in front of the foods, choose an **action** (such as
**Delete selected foods**) and apply it. Be careful: deleting is permanent. To take
foods out of the app, prefer hiding them one by one (5.7).

If your selection includes a **user's custom food**, the whole delete is refused
("forbidden"): untick it, or filter **owner → Global** first.

### 5.10 You cannot

- **Edit or delete a user's custom food.** It is private to that user; they manage it in the app.

---

## 6. Meals

**What it is.** Every meal users log: breakfast, lunch, dinner, snacks, with the
foods they ate. A meal is logged by hand in the app or with the AI ("اكتب أكلك");
AI meals are always **confirmed by the user** before they are saved.

| Column | Meaning |
|--------|---------|
| User | The user's email |
| Date | The day of the meal (user's time zone) |
| Meal type | breakfast, lunch, dinner, snack |
| Source | *Manual* (typed by the user) or *Agent* (prepared by the AI, then confirmed by the user). Photo and voice will appear later. |
| Created at | When it was logged |

**How to use it, step by step:**

1. Click **Health → Meals**.
2. Search the user's **email**; use the **dates** row to go to the day you want.
3. Click a meal to open it. The **items** table shows each food of the meal:
   name, portion, quantity, grams, kcal, protein, carbs, fat.
4. To check a user's calories for a day, add up the **kcal** of that day's meals
   (the app shows this total to the user on their calorie ring).

**Good to know:**
- The numbers on a meal are the ones **at the moment it was logged** (see 5.5).
  If they look outdated compared with the food today, that is normal.
- If the food was deleted later, the meal still shows its name and numbers.
- Users can add, change or delete their meals for **today and yesterday** only.
  Older meals are frozen.

**You cannot** change meals here. *Why?* They are the user's diary; the app checks
dates and recalculates totals when users edit them.

---

## 7. Metric trackables

**What it is.** When a user confirms their plan, the app creates four daily
counters for them: **water**, **sleep**, **steps** and **activity**, each with the
target from the plan. This page shows the link between each user and their four
counters. You will rarely need it.

| Column | Meaning |
|--------|---------|
| User | The user's email |
| Metric | water, sleep, steps or activity |
| Trackable | The counter's name (to see it, open *Tracking → Trackables* and search the user's email) |

**How to use it:** search a user's email to check they have their four counters.
A user who never confirmed a plan has none; that is normal.

**Good to know:** in *Tracking → Trackables* these four counters show **Managed by:
health**. Their targets, units and schedule are locked there, because they always
follow the user's plan. To change a target, the **user** changes their plan in the app.

**You cannot** change anything here.

---

## 8. Common situations: what to do when…

**A user says a food is missing in the app.**
1. Search it in **Foods**, trying other spellings.
2. Found it, but hidden? Tick **Is active** and save.
3. Found it, but under another name? Add the user's word to **Aliases** and save.
4. Not found? **Add food** (section 5.4).

**A user says a food's calories are wrong.**
Open the food, correct the numbers (per 100 g!), save. New meals use the new values;
past meals keep theirs (section 5.5). If you verified the numbers, tick **Is verified**.

**A user says their calorie target is strange.**
Open **Plans**, search their email, open the newest version. Check goal, mode,
activity and whether **Calorie floor applied** is Yes. If they typed their calories
manually (*Manual* mode), the target is what they typed. They can recalculate from
the app.

**A user says yesterday's meal disappeared.**
Open **Meals**, search their email, go to that date. If it is not there, the user
(or someone using their account) deleted it in the app; meals cannot be deleted from
the admin.

**A user can't add a meal for three days ago.**
That is the rule: meals can be logged for **today and yesterday** only, so the
diary stays honest.

**I want to prepare foods before launch.**
Add the most common foods with their portions (5.4), or send the list to your
developer for an import (5.8). Hide the test foods (keys starting with `test-`).

---

## 9. Words you will see

| Word | Meaning |
|------|---------|
| kcal | Kilocalories, the usual "calories" |
| Protein g / Carbs g / Fat g | Grams of protein, carbohydrates, fat |
| Per 100 g | The values for 100 grams of the food; the app scales them to the portion eaten |
| Portion | A usual serving with its weight (1 plate = 350 g) |
| Alias | Another name for the same food (فلافل = طعمية) |
| Verified | The numbers were checked against a trusted source |
| Active / inactive | Visible / hidden for users |
| Global / custom food | For everyone / private to one user |
| Source (of a meal) | How it was logged: manual or by the AI (agent) |
| Effective from | The first day a version of a plan applies |
| Calorie floor | The minimum safe daily calories (1500 men, 1200 women) |
| Time zone | Each user's days follow their own clock (Cairo, Paris…), not the server's |
