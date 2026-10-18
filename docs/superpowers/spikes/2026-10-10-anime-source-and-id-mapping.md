# Anime source and ID mapping: spike report

Date run: 2026-10-18. Plan: `docs/superpowers/plans/2026-10-10-plan-0-anime-source-spike.md`. The spike code lives in `spikes/anime_mapping/` and is throwaway.

## Question

Which providers supply anime franchise structure, episode lists with air dates, and a bridge to TMDB ids, so that the data model and provider contracts rest on measured facts?

## Method

- 12 fixtures (see `spikes/anime_mapping/fixtures.py`): Frieren, Attack on Titan, One Piece, FMA: Brotherhood, Bakemonogatari, Fate/stay night, Steins;Gate, Demon Slayer, Jujutsu Kaisen, Mushishi, Cowboy Bebop, Your Name.
- Franchise structure from AniList relations (no key). ID bridge from `Fribb/anime-lists` (`anime-list-full.json`, 39,619 entries). Probes with a Simkl client id and a TMDB read token (both available). TVDB was not probed (excluded by the spec). Jikan was unreachable and Kitsu timed out from the development machine.
- Three comparisons: the AniList grouping against the Yamtrack fork's relation rules; a TMDB-only episode allocation (all seasons concatenated and sliced, or Fribb season and offset) against Simkl, scored by whether the first allocated episode aired within 14 days of the AniList start date.

## Results

### Coverage

| Title | Main entries | Extras | In Fribb | With Simkl id | With TMDB id | With IMDb id | AniList upcoming episodes | Simkl relations | Simkl episodes (dated) | TMDB seasons |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Frieren | 3 | 3 | 3/3 | 3/3 | 3/3 | 3/3 | 0 | 2 | 53 (53) | 2 |
| Attack on Titan | 9 | 7 | 8/9 | 8/9 | 7/9 | 7/9 | 0 | 12 | 40 (35) | 5 |
| One Piece | 2 | 47 | 2/2 | 2/2 | 2/2 | 2/2 | 9 | 26 | 1235 (1227) | 24 |
| Fullmetal Alchemist: Brotherhood | 1 | 4 | 1/1 | 1/1 | 1/1 | 1/1 | 0 | 3 | 86 (85) | 2 |
| Bakemonogatari | 13 | 5 | 13/13 | 9/13 | 9/13 | 9/13 | 0 | 13 | 18 (18) | 6 |
| Fate/stay night: UBW | 9 | 10 | 9/9 | 6/9 | 6/9 | 6/9 | 0 | 45 | 14 (14) | 3 |
| Steins;Gate | 3 | 2 | 3/3 | 2/3 | 2/3 | 2/3 | 0 | 7 | 27 (27) | 2 |
| Demon Slayer | 9 | 0 | 9/9 | 7/9 | 7/9 | 7/9 | 0 | 16 | 26 (26) | 6 |
| Jujutsu Kaisen | 6 | 0 | 6/6 | 6/6 | 5/6 | 5/6 | 0 | 6 | 46 (46) | 2 |
| Mushishi | 6 | 0 | 6/6 | 5/6 | 5/6 | 5/6 | 0 | 4 | 39 (36) | 3 |
| Cowboy Bebop | 1 | 3 | 1/1 | 1/1 | 1/1 | 1/1 | 0 | 1 | 40 (28) | 2 |
| Your Name | 1 | 0 | 1/1 | 1/1 | 1/1 | 1/1 | 0 | 2 | 14 (14) | n/a |

### Hand-checked chains

Hand-check (2026-10-18 run):
- Attack on Titan: chain correct and ordered; 9 entries = 6 TV cours + OVA + 2 specials (Final Season Kanketsu-hen). Fribb gives season+offset (S3 pt2 off 12, Final Season pt2 off 16, Kanketsu off 28).
- Bakemonogatari: 13 entries in release order; Fribb maps Nekomonogatari (Kuro), Hanamonogatari, Tsukimonogatari to TMDB season 0 (specials); 4 of 13 have no TMDB/Simkl id (3 Kizumonogatari movies + Zoku Owarimonogatari OVA).
- Fate/stay night: sequel/prequel chain merges 4 adaptations (2006, Zero, UBW, Heaven's Feel) under 3 different TMDB shows (37858, 45845, 61415); entries with Fribb season=1 for several shows. Heaven's Feel movies have no TMDB id.
- Demon Slayer: TV cours map to TMDB seasons 1-5 cleanly; compilation movies map to TMDB movie ids; two upcoming movies have no start date and no id.

### Comparison with the Yamtrack fork's rules

| Title | ours main | fork main | only ours | only fork | extras ours/fork | skipped edges |
| --- | --- | --- | --- | --- | --- | --- |
| Frieren | 3 | 3 | [] | [] | 3/3 | 0 |
| Attack on Titan | 9 | 11 | [] | [20691, 20692] | 7/4 | 5 |
| One Piece | 2 | 2 | [] | [] | 47/46 | 2 |
| Fullmetal Alchemist: Brotherhood | 1 | 1 | [] | [] | 4/2 | 3 |
| Bakemonogatari | 13 | 13 | [] | [] | 5/4 | 1 |
| Fate/stay night: UBW | 9 | 9 | [] | [] | 10/1 | 37 |
| Steins;Gate | 3 | 2 | [11577] | [] | 2/2 | 1 |
| Demon Slayer | 9 | 9 | [] | [] | 0/0 | 2 |
| Jujutsu Kaisen | 6 | 5 | [204066] | [] | 0/1 | 2 |
| Mushishi | 6 | 6 | [] | [] | 0/0 | 0 |
| Cowboy Bebop | 1 | 1 | [] | [] | 3/3 | 0 |
| Your Name | 1 | 1 | [] | [] | 0/0 | 0 |

Fork comparison verdicts (9/12 identical main chains):
- 20691, 20692 (AoT Zenpen/Kouhen movies, only fork): recap compilations of season 1, linked by ALTERNATIVE. Fork wrongly promotes them to main by walking through extras; ours keeps them as extras. Ours right.
- 11577 (Steins;Gate Fuka Ryouiki no Deja vu movie, only ours): canon continuation film with PREQUEL OVA + PARENT edge to the TV show. Fork demotes it via PARENT; ours keeps it main. Ours arguably right.
- 204066 (JJK Shibuya Jihen compilation movie, only ours): recap of season 2 arc with SEQUEL+PARENT edges. Fork demotes it to extra. Fork right; ours wrongly main.
- Fate: fork skips 37 ALTERNATIVE/SPIN_OFF edges, ours lists 10 extras vs fork 1.
Net: neither rule is strictly better; a PARENT edge on a MOVIE/compilation is the signal the fork uses well, and walking through extras is the fork's flaw. Candidate rule for plan 2: ours + demote entries having a PARENT edge when format is MOVIE or SPECIAL.

### TMDB-only episode scoring

| Title | entries | concat ok | fribb ok | simkl ok | notes |
| --- | --- | --- | --- | --- | --- |
| Frieren | 3 | 2 | 2 | 2 | Sousou no Frieren 3rd Season (concat first=None) |
| Attack on Titan | 6 | 6 | 6 | 6 |  |
| One Piece | 1 | 1 | 0 | 1 |  |
| Fullmetal Alchemist: Brotherhood | 1 | 1 | 1 | 1 |  |
| Bakemonogatari | 9 | 1 | 4 | 9 | Nisemonogatari (concat first=2012-01-29); Nekomonogatari (Kuro) (concat first=2013-07-28); Monogatari Series: Second Se (concat first=2013-09-01); Hanamonogatari (concat first=2015-12-13); Tsukimonogatari (concat first=2024-07-27); Owarimonogatari (concat first=2024-08-31); Owarimonogatari (Ge) (concat first=None); Monogatari Series: Off & Mon (concat first=None) |
| Fate/stay night: UBW | 5 | 5 | 3 | 4 |  |
| Steins;Gate | 1 | 1 | 1 | 1 |  |
| Demon Slayer | 5 | 5 | 5 | 5 |  |
| Jujutsu Kaisen | 3 | 3 | 2 | 3 |  |
| Mushishi | 3 | 3 | 3 | 3 |  |
| Cowboy Bebop | 1 | 1 | 1 | 0 |  |
| Your Name | 0 | 0 | 0 | 0 |  |
| TOTAL | 38 | 29 | 28 | 35 | |

TMDB-only scoring (38 TV/ONA entries): concat 29, fribb 28, simkl 35. Misses:
- Frieren S3: unaired (start 2027-10), no dates in TMDB or Simkl. Not a method failure.
- Monogatari (8 of 9 concat misses): AniList counts differ from TMDB season sizes (S1 15 vs 12, S3 26 vs 23), so cumulative slicing drifts; four entries sit in TMDB season 0 (specials), which concat excludes. Removing season-0 entries from the stream does not fix it (1 of 6) because the count mismatch remains.
- One Piece under fribb: Fribb has no season for the single absolute-numbered entry; concat works.
- Cowboy Bebop under simkl: first non-special episode has no date.
Ad-hoc hybrid (try Fribb season/offset, fall back to concat, accept the first candidate whose first air date is within 14 days of the AniList start): 32 of 38 (84%). Remaining misses: Frieren S3 (unaired), Nekomonogatari, Hanamonogatari, Tsukimonogatari (TMDB season-0 specials, not fetched), Owarimonogatari (Ge) and Off & Monster Season (TMDB dates differ from AniList by months).
Takeaway: a date-validated fallback chain is the guard; ~16% of Monogatari-like entries stay unresolved and need an AniList-only degraded mode (counts and dates, no titles). Simkl resolves 35 of 38 but carries the login obligation.

## Findings

- **Coverage.** Of 63 main-chain entries, 62 (98%) have a Fribb record, 51 (81%) a Simkl id, 49 (78%) a TMDB id and 49 an IMDb id. Every one of the 39 TV and ONA entries has both a TMDB and a Simkl id. The gaps are compilation movies (8 of 17 movies have a TMDB id), OVAs (0 of 3) and some specials (2 of 4). The weakest fixtures are Bakemonogatari and Fate/stay night (9 of 13 and 6 of 9 with ids), because of unlisted movies and OVAs.
- **Season and offset.** Fribb's `season` and `episode_offset` map Attack on Titan exactly (season 3 part 2 at offset 12, the final season part 2 at offset 16, the Kanketsu-hen special at offset 28). Overall, 27 of 34 checked TV entries fit their TMDB season length (3 more could not be checked: unknown episode count or no matching TMDB season). The misses are Fate (Fribb attaches several adaptations to one TMDB season), Bakemonogatari (AniList episode counts exceed TMDB season sizes) and Jujutsu Kaisen (TMDB has one 59-episode season where Fribb says season 2). So Fribb alone is not enough; the date check is needed.
- **AniList chain.** For the four hand-checked titles the sequel/prequel chain is right and ordered by start date. Against the Yamtrack rules 9 of 12 fixtures have identical main chains. Where they differ, the fork is wrong on two Attack on Titan recap movies (it walks through extras) and right on the Jujutsu Kaisen Shibuya compilation movie (a PARENT edge on a movie marks a recap); our grouping is arguably right on the Steins;Gate continuation film. Neither side is strictly better.
- **Air dates.** AniList `airingSchedule` has data only for running shows (9 entries for One Piece, 0 elsewhere). TMDB season episodes carry air dates, titles, overviews and stills for aired episodes. Simkl has dates for most episodes but not all (Attack on Titan 35 of 40, Cowboy Bebop 28 of 40). Unaired seasons (Frieren season 3) have no dates anywhere.

## Decision rules and outcome

- **Franchise source: AniList.** The chain is right for the four hand-checked titles, and for the other eight it agrees with the fork's independent rules except in three entries, all classified above. Our grouping has one confirmed wrong main entry (the Jujutsu Kaisen Shibuya compilation movie), which the PARENT rule below fixes; the fork has two (Attack on Titan recap movies). 11 of 12 fixtures are fully right, which meets the 10-of-12 threshold. Adopt the fork's PARENT rule on top: an entry whose format is MOVIE or SPECIAL and that has a PARENT edge is an extra, not a main entry.
- **ID bridge: Fribb.** Coverage is 98% of main-chain entries, above the 90% threshold. Refresh the dataset on a schedule and do not trust its season and offset blindly (see below).
- **Simkl necessity: not needed for the MVP.**

  | Field per episode | Source without Simkl |
  | --- | --- |
  | Title, overview, still | TMDB (aired episodes); AniList `streamingEpisodes` as a thin fallback |
  | Air date (aired) | TMDB |
  | Air date (upcoming) | AniList `airingSchedule`, TMDB |
  | Count | AniList `episodes` |

  Simkl resolves 35 of 38 TV/ONA entries by the date check against 29 for plain TMDB concatenation, but its catalog use carries the login-and-sync obligation (Simkl API rule 2) and episode lists have no stable update contract. A hybrid on TMDB, trying Fribb's season and offset first and then concatenation, accepting the first candidate whose first air date is within 14 days of the AniList start date, resolves 32 of 38 (84%). The rest (Monogatari-style entries, unaired seasons) fall back to a degraded mode: episode numbers and dates from AniList, no titles, flagged as such.
- **Episode source: TMDB**, validated by the date check, with the degraded AniList mode. Simkl remains a possible later addition only if Simkl login and sync are built; that needs its own decision.

## Consequences for the spec

Edits applied to `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`:

- Section 4: the provider table drops MAL and Simkl and adds AniList (anime entries, relations, upcoming schedule, anime rating) and Fribb (ID bridge). TMDB also supplies anime episodes. The paragraph on id mapping now describes the bridge, the date check and the degraded mode.
- Section 13: the finished spike item is removed. New open items: the Fribb refresh schedule and the degraded-mode UX, whether to add the Simkl provider later with login and sync, and the PARENT rule on movies and specials.
