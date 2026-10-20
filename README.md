# listam-cli

[![tests](https://github.com/haykarabyan/listam-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/haykarabyan/listam-cli/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A command-line interface for [list.am](https://www.list.am), Armenia's largest classifieds site, built for AI agents.
Search any category, filter car parts by the vehicle they fit, and get listing details, photos and seller phone
numbers as JSON.

> Unofficial and not affiliated with list.am. list.am's Terms of Service ask users not to "use automated programs
> to gain access to the site", so use this at your own risk, keep usage low and personal, and don't harvest seller
> contacts in bulk. Posting acts on your own account, which list.am could restrict.

## Install

Requires Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/haykarabyan/listam-cli
```

Or run it from a clone with `uv run listam …`.

## Usage

The examples below show real output from October 2026; listings change daily.

Search in Armenian: Toyota Camry headlights ("ֆառ") from sellers in Yerevan.

```console
$ listam search "ֆառ" --make toyota --model camry --location yerevan --sections regular --limit 3 --format text
# total exact matches: 251  results on page(s): 3  next_page: 2  url: https://www.list.am/en/category/22?q=%D6%86%D5%A1%D5%BC&cc_bid=76&cc_mid=1560&n=1
22253770 | regular | 47,000 ֏ | Toyota camry 2015 - 2016 v55 se ֆառ depo | Suitable For Toyota Camry | Erebuni | 1 photos
20789558 | regular | 40,000 ֏ | Toyota camry ֆառ 2009-2011թ | Suitable For Toyota Camry | Erebuni | 1 photos
22287995 | regular | 40,000 ֏ | Toyota camry 2010 - 2011 ֆառ se | Suitable For Toyota Camry | Erebuni | 1 photos
```

Lada Niva parts in Gyumri, filtered by the vehicle they fit.

```console
$ listam search --make vaz --model "2121 niva" --location gyumri --sections regular --limit 3 --format text
# total exact matches: 5  results on page(s): 3  next_page: None  url: https://www.list.am/en/category/22?cc_bid=78&cc_mid=4079&n=51
22777498 | regular | 4,000 ֏ | Լադա վազ 2121 նիվի սրահի բռնակ նաև 2106-2107 | Suitable For VAZ (Lada) 2121 Niva | Gyumri | 3 photos
21571036 | regular | 50,000 ֏ | VAZ (Lada) 2121 Niva դեմի մոստ | Suitable For VAZ (Lada) 2121 Niva | Gyumri | 1 photos
21532552 | regular | 35,000 ֏ | VAZ (Lada) 2121 Niva ռազդատկա | Suitable For VAZ (Lada) 2121 Niva | Gyumri | 1 photos
```

Used Toyota Camrys from 2018 or newer in Yerevan under $25,000 (category 23 is Passenger Cars, `_a2_1` is "Year from").

```console
$ listam search -c 23 --make toyota --model camry -f "_a2_1=2018" --location yerevan --currency usd --price-max 25000 --sections regular --limit 3 --format text
# total exact matches: 528  results on page(s): 3  next_page: 2  url: https://www.list.am/en/category/23?bid=76&mid=1560&n=1&crc=1&price2=25000&_a2_1=2018
995182429 | regular | $ 24,500 | 2020 Toyota Camry, 2.5L, gas | 2020 y., 105,000 km, Gasoline, RU Luxe Safety | Arabkir | 7 photos
23701294 | regular | $ 19,800 | 2021 Toyota Camry, 2.5L | 2021 y., 63,000 km, Gasoline, US SE | Malatia-Sebastia | 13 photos
21170877 | regular | $ 16,800 | 2018 Toyota Camry, 2.5L | 2018 y., 145,000 km, Gasoline | Kentron | 16 photos
```

Apartments in Kentron under $150,000, sold by owners rather than agencies.

```console
$ listam search -c 60 --location kentron --currency usd --price-max 150000 -f cmtype=owner --sections regular --limit 3 --format text
# total exact matches: 26  results on page(s): 3  next_page: None  url: https://www.list.am/en/category/60?n=8&crc=1&price2=150000&cmtype=0
995191445 | regular | $ 150,000 | Studio apartment on Bryusov Street in the center, 35 sq.m., second to last floor | Studio, 35 sq.m., 8/9 floor | Kentron | 10 photos
24100506 | regular | $ 148,000 | 1 room apartment on Zavaryan Street in the center, 42 sq.m., 4/9 floor | 1 rm., 42 sq.m., 4/9 floor | Kentron | 16 photos
24035931 | regular | $ 139,000 | 3 room apartment on Khorenatsi Street in the center, 39 sq.m., stone building | 3 rm., 39 sq.m., 5/5 floor | Kentron | 14 photos
```

Details of one listing, including the exact generation it fits and full-size photo URLs.

```console
$ listam item 22253770 --format text
Toyota camry 2015 - 2016 v55 se ֆառ depo
id 22253770  47,000 ֏  Yerevan › Erebuni
https://www.list.am/en/item/22253770
Manufacturer: Other
Condition: New
Car Part Side: Left side
Fits: Toyota Camry XV50 Restyling (2014 - 2018)

Toyota camry 2015 - 2016 v55 se ֆառ depo բոլորովին նոր շատ լավ որակի տեղադրվում է առանց խնդիրների առանց զազոռների նուն օրիգինալի նման

Seller: Auto Parts  https://www.list.am/user/2795549
Image: https://img.list.am/f/041/87719041.jpg
```

Add `--phone` to include the seller's numbers (local and `+374…`, plus Viber/WhatsApp links), or `--download DIR`
to save every photo. `listam phone ID` returns just the contacts.

| Command | Purpose |
|---|---|
| `search [QUERY]` | Search listings. Filters: `-c` category, `--make/--model` (cars, or the car a part fits), `--generation` (parts), `--brand`, `--condition`, `--location`, `--price-min/--price-max`, `--currency`, `-f KEY=VALUE` |
| `item ID` | Full details; `--phone` adds contacts, `--download DIR` saves all photos |
| `phone ID` | Phone numbers (local and `+374…`), Viber, WhatsApp |
| `images ID` | Download all photos |
| `categories [ID]` | Category tree |
| `filters CATEGORY` | A category's filter params and option ids |
| `makes`, `models MAKE`, `generations MAKE MODEL` | Vehicle lookups for fitment filters |
| `login`, `logout`, `whoami` | Account session (see [Posting](#posting-your-own-ads-beta)) |
| `post-form CATEGORY`, `post SPEC` | Describe a category's ad form; post an ad as draft or published |
| `my`, `delete ID` | List and delete your own ads |

- Output is JSON (`--format text` for a compact summary). Errors go to stderr as JSON; exit codes are `0` ok,
  `1` error, `2` not found, `3` blocked by Cloudflare, `4` not logged in.
- Filter values accept names or ids. Category-specific filters (`_aNN`) are listed by `listam filters <category>`.
- Search results are tagged `regular` (real matches), `top` (paid promotion) or `similar` (suggestions).
- `--lang en|hy|ru` switches the UI language. Run `listam <command> --help` for all options.

## Posting your own ads (beta)

```bash
listam login                                   # phone/email + password; stores session cookies only
listam post-form 1679 --template > ad.json      # starter spec for a category (1679 = Brake Pads and Shoes)
listam post ad.json --dry-run                   # validate and resolve names to ids, submit nothing
listam post ad.json --draft                     # save as a draft on your account
listam post ad.json                             # upload photos, preview, confirm y/N, publish
listam my --tab drafts                          # active | drafts | moderation | inactive | rejected
listam delete 3858824                           # asks before deleting
```

An ad spec is JSON; values may be option names or ids:

```json
{
  "category": 1679, "type": "sale",
  "title": "Toyota Camry XV70 front brake pads", "description": "New, original packaging.",
  "price": 15000, "currency": "amd", "location": "Arabkir",
  "photos": ["front.jpg", "box.jpg"], "phone": true,
  "part_number": "04465-33450",
  "fits": [{"make": "toyota", "model": "camry", "generation": "xv70"}],
  "fields": {"condition": "new", "manufacturer": "Bosch", "car_part_side": "not applicable", "car_part_position": "front"}
}
```

- **Login**: the session is saved to `~/.config/listam/session.json` (readable only by you); the password never is.
  If you sign in with Google/Facebook, import your browser session instead: run `copy(document.cookie)` in the
  DevTools console on list.am, then `pbpaste | listam login --cookies --password-stdin`. Check with `listam whoami`.
- **Safety**: paid promotions are never selected, publishing asks for confirmation (`--yes` skips it), and a
  cancelled publish deletes the draft list.am creates during preview.
- **Photos**: JPEG up to 16 MB, or install `listam-cli[photos]` (Pillow) to convert and resize to 1280×960.
- **Not supported yet**: categories whose form loads fields dynamically (car listings), and editing existing ads.
  Drafts and the preview step are tested end to end; the final publish click has not been exercised yet.

## Using with AI agents

[`skills/listam/SKILL.md`](skills/listam/SKILL.md) teaches an agent the workflow, part vocabulary in
Armenian/Russian, and category ids. For Claude Code:

```bash
ln -s "$(pwd)/skills/listam" ~/.claude/skills/listam
```

For other agents, copy its contents into your rules or system prompt.

## How it works

list.am has no public API, so the CLI reads the same pages and endpoints the website uses. It passes Cloudflare by
impersonating Chrome with [curl_cffi](https://github.com/lexiforest/curl_cffi), spaces requests about 0.4 s apart,
and caches filter lists in `~/.cache/listam` for 3 days.

Limitations: no messaging or ad editing yet, sorting is limited to default/relevance, and parsers may
need updating when list.am changes its HTML.

## Development

```bash
uv run --group dev pytest
```

Tests run offline against saved list.am pages in `tests/fixtures/`, with seller details replaced by placeholders.
Pull requests are welcome, especially parser fixes when list.am changes its layout.

## License

[MIT](LICENSE)
