---
name: listam
description: Search list.am (Armenia's main classifieds site) via the `listam` CLI — find car spare parts or any other listings, inspect photos, specs and vehicle fitment, get seller phone numbers (plus Viber/WhatsApp), and draft or post the user's own ads. Use when the user wants to buy/find something on list.am, compare offers, contact a seller, or sell something.
---

# list.am via the `listam` CLI

## Running it

If `listam` is on PATH (`uv tool install`), call it directly. Otherwise run it from the repo with
`uv run --directory <path-to-listam-cli> listam …` — written as just `listam` below. With `--directory`, relative
paths resolve inside the repo, so pass absolute paths to `--download` / `--dir`.

`listam` returns JSON on stdout (errors as JSON on stderr; exit 2 = not found, 3 = Cloudflare block → wait and retry).
Run `listam <command> --help` for every flag.

## Typical car-part workflow

1. **Narrow by vehicle, not just keywords.** Fitment filters are far more reliable than text search,
   because sellers write titles in Armenian, Russian or translit.
   ```bash
   listam search --make toyota --model camry --generation xv70 -c 540 --format text
   listam search "headlight" --make toyota --model camry --condition used --format text --pages 2
   ```
   - `--make/--model/--generation` accept names or ids (`listam makes`, `listam models toyota`, `listam generations toyota camry`).
     Ambiguous names return an error listing the candidates — pick one and retry.
   - `-c <category>` narrows to a part type (table below). Without `-c`, `--make` searches all Parts and Accessories (22).
   - Other filters: `--condition new|used`, `--brand Bosch` (part manufacturer), `--location Yerevan|Arabkir|Gyumri…`,
     `--price-min/--price-max` (AMD unless `--currency usd`), `--availability sale|order`.
2. **Read results.** `--format text` gives one line per result: `id | section | price | title | fitment | location | photos`.
   - `section`: `regular` = real matches (count in `total_matches`), `top` = paid promotion, `similar` = fuzzy suggestions
     (often still relevant — check them). Use `--sections regular` to drop the rest.
   - JSON results include `images` (full-size URLs of up to ~6 preview photos) and `thumbnail`.
3. **Inspect a shortlisted listing**:
   ```bash
   listam item <id or URL> --phone --download /tmp/listam
   ```
   Returns title, price (+ USD/RUB conversion, price history), attributes (condition, side, position, OEM part number,
   or RAM/storage/screen for electronics…),
   `compatibility` (vehicles + generations it fits), description, seller, **all** photo URLs, local `image_files`,
   and `contact.phones[]` with `display`, `local` and `e164` (+374…) numbers plus `viber`/`whatsapp` links.
   **Look at the downloaded photos** (Read the image files) to judge condition and confirm it is the right part.
4. **Give the user** the shortlisted ads with price, condition, fitment, location, URL, photos you checked, and phone
   numbers in `e164` form, plus questions worth asking the seller (exact year/trim fit, OEM vs copy, warranty, installation, delivery).

Only call `phone` / `item --phone` for listings the user is actually considering — don't bulk-reveal numbers.

Sanity-check listings before recommending: specs that contradict each other (e.g. a chip/size combination that does
not exist), a price far below the rest, stock photos instead of real ones, or a very new seller account are red flags.
On `--lang en`/`ru` descriptions are machine-translated (`description_translation` says from what).

## Search vocabulary

Text search matches titles literally, so if English returns little, retry with local words:

| English | Armenian | Russian / translit |
|---|---|---|
| headlight | լուսարձակ, ֆառ | фара, far |
| tail light | ստոպ, հետևի լապտեր | стоп, фонарь |
| brake pads | արգելակման կոճղակ, կալոդկա | колодки, kalodka |
| brake disc | արգելակման սկավառակ, դիսկ | тормозной диск |
| shock absorber | ամորտիզատոր | амортизатор |
| CV joint | գռանատ | граната |
| radiator | ռադիատոր | радиатор |
| bumper | բամպեր | бампер |
| side mirror | հայելի, զերկալո | зеркало |
| hood | կապոտ | капот |
| door | դուռ | дверь |
| filter | ֆիլտր | фильтр |
| engine | շարժիչ, մատոռ | двигатель, мотор |

`--lang ru` / `--lang hy` switches UI labels (location names, attribute names) but not seller-written titles.

## Category ids (car-related)

| id | category |
|---|---|
| 22 | Parts and Accessories (all) |
| 530 | Car Parts |
| 531 | Engine Parts |
| 532 | Body Parts |
| 533 | Chassis |
| 534 | Steering Systems |
| 535 | Braking Systems (1679 pads, 1680 discs/drums, 1681 calipers…) |
| 536 | Exhaust Systems |
| 537 | Fuel Systems |
| 538 | Transmission |
| 539 | Electrical Systems |
| 540 | Lighting and Optics |
| 541 | Car Interior |
| 542 | Car Glass |
| 543 | Cooling and Heating Systems |
| 544 | Safety |
| 545 | Everything Else |
| 115 / 238 | Wheels and Tires / Rims and Hub Caps |
| 236 / 215 | Car Batteries / Oils and Chemicals |
| 83 / 193 / 194 | Car Accessories / Car Electronics / Audio |

Discover others with `listam categories` (top level) and `listam categories <id>` (children).

## Buying a car

In vehicle categories (`23` Passenger Cars, `25` Trucks, …) `--make/--model` filter the vehicle itself.
Year, mileage, engine etc. are range filters named `_aN_1` (from) / `_aN_2` (to); see `listam filters 23`.
```bash
listam search -c 23 --make toyota --model camry -f "_a2_1=2018" --location yerevan --currency usd --price-max 25000 --format text
```
`listam item` returns specs in `attributes` (e.g. "Cleared Customs", "Current Condition", "Mileage") and
equipment in `features` (e.g. "Heated seats").

## Other categories

The CLI works for any list.am category. Category-specific filters (RAM, processor, rooms…) are named `_aNN`;
discover them with `listam filters <category>` and pass them with `-f KEY=VALUE` (VALUE = option name or id):
```bash
listam categories 98                                   # Laptops and Computers -> Notebooks = 116
listam filters 116 --name _a138                        # processor options
listam search -c 116 --brand apple -f "_a138=Apple M5 Pro" --condition used --sections regular --format text
listam search -c 62 -f cmtype=owner --location arabkir --currency usd --price-max 120000 --format text
```
Results from a multi-category search carry `category_path` (e.g. "Furniture › Sofas"). Job cards put the employer
in `subtitle` and schedule/experience in `labels`; service and job cards usually have no price.
`--condition` resolves against the category's own options (e.g. new/used/faulty for laptops, finished/unfinished
for real estate). `--brand` uses the category's Make/Manufacturer list.
Sellers don't always fill in structured fields, so also run a text search (e.g. `"macbook pro m5"`) and merge.

## Paging

`--page N` starts at page N, `--pages K` fetches K pages and merges them. JSON includes `has_next_page` and `next_page`.
A page holds ~100 cards. Note `has_next_page` can be true even when `total_matches` is small (extra pages are "similar").

## Posting the user's ads

Requires `listam login` (the user runs it themselves; never ask for or handle their password). `listam whoami`
checks the session; exit code 4 means not logged in.

1. Find the leaf category (`listam categories …`), then `listam post-form <id> --template` for a spec skeleton and
   `listam post-form <id> --name <field>` for a field's full option list. Errors name the sub-category to use.
2. Write the spec JSON (see the template's `_help`), then `listam post spec.json --dry-run` until it validates.
3. Default to `listam post spec.json --draft` and tell the user the draft is ready to review on list.am or publish.
   Only publish (`listam post spec.json`) when the user explicitly asks; it shows a summary and asks y/N on the
   terminal. Never pass `--yes` unless the user explicitly asked to publish without confirmation.
4. `listam my --tab drafts|active|moderation` shows the result; `listam delete <id>` removes an ad (ask first).

The CLI never selects paid promotions. Car listings (dynamic forms) are not supported; it says so.

