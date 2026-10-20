# Browse and Title Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user can browse movies, shows and anime by type, genre, country and year, open any title (even one nobody has added yet) and see its genres, country, cast, trailers, collection and similar titles, with "See also" links that lead back into Browse.

**Architecture:** Browse is a live, cached view over TMDB `discover` and AniList `Page` queries; nothing it shows is stored. The facts of a title (genres, countries, cast, trailers, collection, similar titles) arrive in the calls the sync already makes (`append_to_response` on TMDB, one extra details query on AniList) and are stored in a new `TitleDetails` row. A title nobody has added is created by an "open" action that runs the same create-and-sync job as the add flow, without a library entry.

**Tech Stack:** Django 5.2, PostgreSQL 17, Redis 7, Huey, HTMX, Alpine, the Modernist design system (`web/static/web/ds.css`, `app.css`), Babel (country names), pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md` (sections 3, 4, 5, 11, 13). The visual reference is `docs/design/` (`Browse.dc.html`, `Collection.dc.html`, `Title Page.dc.html`, `Poster Card.dc.html`); the mockups use inline demo data, so take layout, states and wording from them, not their logic.

## Global Constraints

- Django 5.2 LTS, Python 3.12, PostgreSQL, Redis, Huey on Redis; UI is HTMX + Alpine, no JavaScript build step (spec sections 2 and 5).
- "TitleTranslation: lazily filled persistent cache per language. Search results are not persisted." (spec section 3). In this plan Browse and Collection results are never stored either; a `Title` is created only by the add flow or by the open action (Decision 5).
- "Cache layers: Postgres (durable), Redis TTL for responses, a per-provider rate limiter, a single-flight lock, and stale-on-error with Huey retry." (spec section 4). Every provider call goes through `ProviderHttp`; this plan adds no other cache.
- "Every screen has loading, empty and error states. Touch targets are at least 44 px." (spec section 5).
- "The UI ships in Russian and English on the Django i18n foundation. Metadata language comes from the profile and is separate from the interface language." (spec section 11). Every new string goes through `{% trans %}` or `gettext` and gets a Russian translation in `sources/locale/ru/LC_MESSAGES/django.po`. The existing `(msgid, msgstr)` set, obsolete `#~` entries included, must stay intact.
- "CSRF protection covers HTMX requests." (spec section 10). Anything that writes is a POST; the open action is a GET that only enqueues a job keyed by a validated provider and id, and it never reads or writes user data.
- All reads of `LibraryEntry`, `WatchEvent` and `AddRequest` go through `Model.objects.for_user(user)` (`tests/test_tracking_scoping.py` enforces it; new code that touches them must pass it).
- Posters and photos are hotlinked with `referrerpolicy="no-referrer"` and `loading="lazy"`. Nothing from YouTube is requested before the user presses play.
- Single quotes for Python strings (docstrings use triple double quotes); type annotations on public functions; Google-style docstrings; PEP 8 with 88 columns preferred; log with `logging` and %-formatting.
- Everything committed (code, comments, commit messages, docs) is in English.
- Run every command from the repository root; tests need the dev containers (`docker compose -f docker-compose.dev.yml up -d --wait`).

## Decisions

These are rulings the implementer cannot make alone; they are binding for this plan.

1. **Scope.** Browse, the Collection page and the title-page enrichment (genres, country, cast, trailers, similar titles, collection card, "See also"). Not built: curated collections (a non-goal), "Mark all as watched" on the Collection page (it would add many titles at once and the add flow caps pending requests at 5; recorded in spec section 13), the section chip bar of the title page, demonym headings ("Icelandic dramas"; the heading lists the filters instead), notifications, import and export.
2. **Caching is the existing provider cache, with longer lifetimes where the data barely changes.** Discover pages are cached 3 hours (`DISCOVER_TTL`), a collection 7 days (`COLLECTION_TTL`), AniList details 24 hours; stale-on-error serves the last copy when a provider is down. A discover payload already carries everything a card shows (title in the user's language, year, poster, rating), so there is no per-card cache and no table of browse results. The facts of a title ride on the sync's own calls, so enrichment adds no TMDB request and one AniList request per anime sync.
3. **Browse parameters.** `GET /browse/?type=movie|tv|anime&genre=<slug>&country=<ISO 3166-1 alpha-2>&year=<YYYY>&sort=popularity|date|title|rating&page=<n>`. A parameter with an unknown value is ignored as if absent (never a 500). Without `type`, the three sources are queried and interleaved page by page, so an order such as "title" is only approximate across sources. TMDB hits that are Japanese animation are dropped (AniList covers them, as in search); AniList discover drops adult entries and entries that have a PREQUEL, so a franchise appears once, as its first entry. The anime source is skipped when the country is not JP, KR, CN or TW or when the genre has no AniList equivalent. Sorting by rating on TMDB requires at least 100 votes.
4. **Facets.** Genres are a fixed vocabulary of slugs (`catalog/vocab.py`) mapped from TMDB genre ids and AniList genre names; unmapped genres are dropped. Countries are ISO codes; names come from Babel in the interface language. The Browse country list is a curated set of 30 codes; years run from the current year down to 1950. Labels follow the interface language, titles follow the metadata language.
5. **Opening a title nobody has added.** Every card of Browse, Collection and "Similar" links to the title page. For a title that is not in the catalog the link is `GET /open/<provider>/<id>/`: it queues `open_title_task` (create the placeholder, sync it, no library entry), shows a page that polls until the external id exists, then redirects to the title page, which polls the sync itself. A failed job removes the placeholder it created. The job is claimed per provider id for 10 minutes and releases the claim when it ends, so a page that finds no title and no claim knows the job failed. Cost if wrong: a signed-in user can make the service create catalog titles for real provider ids; the same is already true of the add flow.
6. **Enrichment storage.** One `TitleDetails` row per title (`genres`, `countries`, `cast`, `trailers`, `similar`, `collection`, all JSON). It is written by the sync and replaced as a whole. Titles synced before this plan have no row; the migration marks them due so the scheduled sync fills them gradually, and the page simply omits what is missing.
7. **Similar titles** are TMDB `recommendations` (not `similar`, which is keyword noise) and AniList `recommendations`, stored in English with the original title; the page shows the localized name when the title is already in the catalog. TMDB recommendations that are Japanese animation are dropped; an anime title takes its list from AniList only, minus the entries of its own franchise.
8. **Anime enrichment comes from the first entry of the main chain** (genres, country, trailer, cast, recommendations). The cast of an anime is its main characters with their Japanese voice actors.
9. **Trailers** are YouTube videos of type Trailer, Teaser or Clip (AniList: the entry's trailer when its site is `youtube`), at most 8, official first, then Trailer before Teaser before Clip. The key must match `[A-Za-z0-9_-]{6,20}` when stored and again when rendered. The player is an `<iframe>` on `www.youtube-nocookie.com` that Alpine inserts after the click; the card is a tinted placeholder with a Play button, so no image is requested from YouTube. Trailers show for every kind, after the episodes or the movie block (the mockup shows them for movies only; this is a call of this plan).
10. **Collections.** A movie belongs to at most one TMDB collection (`belongs_to_collection`). The title page shows one card (name and poster) linking to `/collections/<tmdb_id>/`; the Collection page is a live cached TMDB call with the parts sorted by release date, the user's progress ("2 of 4 completed") and the add picker on each row.
11. **Library state on Browse and Collection rows** is decorated the way search results are (in library: the status; add pending: the progress control; otherwise the add picker), by the code that this plan moves out of `search.py` into `web/views/hits.py`.
12. **Entry points.** Browse has no tab in the bottom bar. It is reached from "See also" and from genre and country tags on the title page, and from one link on the idle Search page.

## Review Focus

- A provider that is down, slow or returns nothing must give partial results with a notice, or an error state that keeps the filters, never a 500: `test_browse_with_one_source_down`, `test_browse_error_keeps_the_filters`, `test_browse_survives_a_redis_outage` (Task 8).
- Hostile or sloppy query strings (`year=abc`, `genre=<script>`, `page=99999999`, `country=ZZ`, `sort=x`, repeated keys) must be ignored, not break the page or reach a provider: `test_browse_ignores_invalid_parameters` (Task 8), `test_browse_query_clamps_the_page` (Task 5).
- Opening an unknown title must not create duplicates on double clicks, must not trust the browser for anything but a provider id, and must leave no placeholder when the provider fails: `test_open_enqueues_once`, `test_failed_open_leaves_no_placeholder`, `test_open_redirects_to_an_existing_title` (Task 7).
- A title with no enrichment (synced before this plan, or the provider returned nothing) must still render completely: `test_title_page_without_details` (Task 9).
- A trailer must never load anything from YouTube before the click, and a bad key must not render: `test_trailers_load_nothing_before_the_click`, `test_a_bad_trailer_key_is_not_rendered` (Task 9).

---

## File Structure

```
sources/catalog/vocab.py                 genre slugs, provider mappings, country names, years
sources/catalog/models.py                TitleDetails
sources/catalog/migrations/0003_...      TitleDetails; 0004_...: admin view permission, resync of synced titles
sources/catalog/admin.py                 TitleDetails, read-only
sources/catalog/sync/common.py           write_details, trailer_dicts, similar_dicts
sources/catalog/sync/{movies,tv,anime}.py   fill TitleDetails
sources/catalog/tasks.py                 open_title_task, enqueue_open_once, open_key
sources/providers/types.py               SearchHit.rating, HitPage
sources/providers/tmdb.py                enriched movie/tv, discover, collection
sources/providers/anilist.py             details, discover
sources/providers/browse.py              BrowseQuery, BrowseResults, browse()
sources/tracking/adding.py               discard_placeholder (was private)
sources/web/views/hits.py                ResultRow, decorate() moved out of search.py
sources/web/views/{browse,collections,opening}.py   the three new screens
sources/web/views/titles.py              enrichment context
sources/web/templates/web/               browse.html, _browse_results.html, _browse_card.html,
                                         collection.html, opening.html, _title_facts.html,
                                         _see_also.html, _cast.html, _trailers.html,
                                         _collection_card.html, _similar.html
sources/web/static/web/app.css           styles (mt- prefix)
sources/locale/ru/LC_MESSAGES/django.po  Russian strings
tests/                                   test_vocab.py, test_tmdb.py, test_anilist.py,
                                         test_browse_providers.py, test_sync_*.py,
                                         test_open.py, test_web_browse.py, test_web_collection.py,
                                         test_web_enrichment.py
```

## Definition of done

- `python -m pytest -q` is green; `pre-commit run --all-files` is clean; `python sources/manage.py makemigrations --check --dry-run` reports no changes.
- With a TMDB token (controller only, Task 12): Browse filters work against real TMDB and AniList data in both languages, a movie, a show and an anime title page show their genres, country, cast, trailers and similar titles, the Matrix page links to its collection, and an unknown title opens through the open action.
- No endpoint of this plan reads or writes another user's data; nothing from YouTube loads before the click.
- CI (`Lint`, `Tests`, `CodeQL`) is green after the user pushes.

---

### Task 1: Vocabulary: genres, countries, years

**Files:**
- Create: `sources/catalog/vocab.py`, `tests/test_vocab.py`
- Modify: `sources/requirements.txt` (add `Babel==2.18.0`), `sources/locale/ru/LC_MESSAGES/django.po`

**Interfaces:**
- Produces (all in `catalog.vocab`):
  - `GENRES: dict[str, StrPromise]`: slug to lazily translated label. Slugs: `action adventure animation comedy crime documentary drama family fantasy history horror kids magical-girl mecha music mystery news psychological reality romance sci-fi slice-of-life soap sports supernatural talk thriller war western`.
  - `genres_from_tmdb(ids: Iterable[int]) -> list[str]`: slugs in input order, without duplicates; unmapped ids are dropped.
  - `genres_from_anilist(names: Iterable[str]) -> list[str]`: same for AniList genre names; unmapped names (Ecchi, Hentai, ...) are dropped.
  - `tmdb_genre_filter(slug: str) -> str`: the `with_genres` value, ids joined by `|`, or `''` for an unknown slug.
  - `anilist_genre_name(slug: str) -> str | None`.
  - `genre_label(slug: str) -> str`: the translated label, or the slug itself when unknown.
  - `BROWSE_COUNTRIES: tuple[str, ...]`, `ANILIST_COUNTRIES: frozenset[str]` (`JP KR CN TW`), `country_name(code: str, language: str) -> str` (Babel territory name, the code itself when unknown), `browse_years(today: date) -> list[int]` (current year down to 1950).

**TMDB mapping to implement** (movie and TV ids share one table; `|` means the id carries several slugs):
`28 action, 12 adventure, 16 animation, 35 comedy, 80 crime, 99 documentary, 18 drama, 10751 family, 14 fantasy, 36 history, 27 horror, 10402 music, 9648 mystery, 10749 romance, 878 sci-fi, 53 thriller, 10752 war, 37 western, 10759 action|adventure, 10762 kids, 10763 news, 10764 reality, 10765 sci-fi|fantasy, 10766 soap, 10767 talk, 10768 war`; `10770` (TV Movie) is dropped.
**AniList mapping:** `Action Adventure Comedy Drama Fantasy Horror Music Mystery Psychological Romance Sports Supernatural Thriller` to the slug of the same name; `Sci-Fi sci-fi`, `Slice of Life slice-of-life`, `Mahou Shoujo magical-girl`, `Mecha mecha`.
**Browse countries (30):** `US GB JP KR CN FR DE IT ES RU CA AU IN BR MX SE DK NO FI IS PL TR TH HK TW UA NL BE IE NZ`.

- [ ] **Step 1: Write the failing tests** in `tests/test_vocab.py`:
  - `test_tmdb_genres_map_to_slugs`: `genres_from_tmdb([10759, 18, 10770, 999])` is `['action', 'adventure', 'drama']`; `genres_from_tmdb([28, 10759])` is `['action', 'adventure']` (no duplicate).
  - `test_anilist_genres_map_to_slugs`: `genres_from_anilist(['Slice of Life', 'Ecchi', 'Sci-Fi'])` is `['slice-of-life', 'sci-fi']`.
  - `test_genre_filters`: `tmdb_genre_filter('sci-fi')` is `'878|10765'`; `tmdb_genre_filter('war')` is `'10752|10768'`; `tmdb_genre_filter('nope')` is `''`; `anilist_genre_name('mecha')` is `'Mecha'`; `anilist_genre_name('western')` is `None`.
  - `test_every_slug_is_reachable_and_labelled`: every key of `GENRES` appears in some mapping value, and `genre_label` returns a non-empty string for each.
  - `test_country_names_follow_the_language`: `country_name('JP', 'en') == 'Japan'`, `country_name('JP', 'ru') == 'Япония'`, `country_name('ZZ', 'en') == 'ZZ'`.
  - `test_browse_years`: `browse_years(date(2026, 10, 20))[0] == 2026` and `[-1] == 1950`.
  - `test_russian_labels_exist`: with `translation.override('ru')`, `genre_label('drama') == 'Драма'`.
- [ ] **Step 2: Run to see them fail:** `python -m pytest tests/test_vocab.py -q`. Expected: fails to import `catalog.vocab`.
- [ ] **Step 3: Implement `catalog/vocab.py`** with the signatures above; the mappings are dictionaries at module level. Pin `Babel==2.18.0` in `sources/requirements.txt` and `pip install` it into the project venv. Add the Russian label for each genre slug (`Боевик`, `Приключения`, `Анимация`, `Комедия`, `Криминал`, `Документальный`, `Драма`, `Семейный`, `Фэнтези`, `История`, `Ужасы`, `Детский`, `Махо-сёдзё`, `Меха`, `Музыка`, `Детектив`, `Новости`, `Психологический`, `Реалити-шоу`, `Романтика`, `Научная фантастика`, `Повседневность`, `Мыльная опера`, `Спорт`, `Сверхъестественное`, `Ток-шоу`, `Триллер`, `Военный`, `Вестерн`) to the `.po` file.
- [ ] **Step 4: Run the tests** and `pre-commit run --all-files`. Expected: all pass.
- [ ] **Step 5: Commit**

```bash
git add sources/catalog/vocab.py sources/requirements.txt sources/locale tests/test_vocab.py
git commit -m "feat: add the genre and country vocabulary for Browse"
```

---

### Task 2: `TitleDetails` model, admin and backfill

**Files:**
- Modify: `sources/catalog/models.py`, `sources/catalog/admin.py`
- Create: `sources/catalog/migrations/0003_titledetails.py` (generated), `sources/catalog/migrations/0004_titledetails_permissions_and_resync.py`
- Test: `tests/test_catalog_models.py`, `tests/test_admin_access.py`

**Interfaces:**
- Produces `catalog.models.TitleDetails`: `title` OneToOne to `Title` (`related_name='details'`, `on_delete=CASCADE`), `genres` JSON list of slugs, `countries` JSON list of ISO codes, `cast` JSON list of `{'name', 'role', 'photo_url'}`, `trailers` JSON list of `{'key', 'name', 'kind', 'language', 'official'}` (YouTube only), `similar` JSON list of `{'provider', 'external_id', 'kind', 'title', 'original_title', 'year', 'poster_url', 'rating'}`, `collection` JSON object `{'id', 'name', 'poster_url'}` or null, `updated_at` (auto). All JSON fields default to an empty list (`collection` to null).

- [ ] **Step 1: Write the failing tests.**
  - `test_title_details_defaults` (models): a bare `TitleDetails.objects.create(title=title)` has `genres == []`, `cast == []`, `collection is None`; `title.details` returns it; deleting the title deletes it.
  - `test_the_migration_marks_synced_titles_due`: use `django_db_setup`-independent logic by calling the migration's function (`resync`) on a synced title with `next_sync_at` in two weeks and an unsynced one; afterwards the synced one has `next_sync_at <= now` and the unsynced is untouched.
  - `test_portal_admin_can_view_but_not_change_title_details` (admin access): the admin list `/admin/catalog/titledetails/` is 200 for a portal admin, adding is 403, like the existing catalog tests.
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement.** The model as above. Register `TitleDetails` in the admin the way `Season` is (read-only for the group: no add/change/delete permission is granted, only view). Generate the schema migration with `makemigrations catalog -n titledetails`. Write migration 0004 modelled on `0002_app_admins_catalog_permissions.py`: grant `view_titledetails` to `app-admins` (create the permission if missing, remove it on rollback), and a second `RunPython(resync, noop)` that sets `next_sync_at = now` for every title with `synced_at` not null, so the scheduled sync (20 titles per run) fills the details gradually.
- [ ] **Step 4: Run the tests**, `makemigrations --check --dry-run`, `pre-commit run --all-files`.
- [ ] **Step 5: Commit** `feat: add TitleDetails and schedule a resync of synced titles`.

---

### Task 3: TMDB client: enrichment, discover, collection

**Files:**
- Modify: `sources/providers/types.py`, `sources/providers/tmdb.py`, `tests/fakes.py`
- Test: `tests/test_tmdb.py`

**Interfaces:**
- Produces in `providers.types`: `SearchHit.rating: float | None = None` (added after `anime_like`; a 0 to 10 average, None when nobody voted) and
  `@dataclass(frozen=True) class HitPage: hits: list[SearchHit]; total: int; has_more: bool`.
- Produces in `providers.tmdb`:
  - `TmdbCredit(name: str, character: str, photo_url: str)`, `TmdbVideo(site: str, key: str, name: str, kind: str, official: bool, language: str)`, `TmdbCollectionRef(tmdb_id: int, name: str, poster_url: str)`, `TmdbCollection(tmdb_id: int, name: str, overview: str, poster_url: str, parts: list[SearchHit])`.
  - New defaulted fields on `TmdbMovie` and `TmdbShow`: `genre_ids: tuple[int, ...] = ()`, `countries: tuple[str, ...] = ()`, `cast: tuple[TmdbCredit, ...] = ()`, `videos: tuple[TmdbVideo, ...] = ()`, `recommendations: tuple[SearchHit, ...] = ()`; `TmdbMovie` also `collection: TmdbCollectionRef | None = None`. (Defaults keep `tests/fakes.py` builders and every existing test working.)
  - `TmdbClient.discover(kind: str, *, genre: str | None, country: str | None, year: int | None, sort: str, page: int, language: str) -> HitPage` with `kind` `'movie'` or `'tv'`, `genre` a slug, `sort` one of `popularity date title rating`.
  - `TmdbClient.collection(tmdb_id: int, language: str = 'en') -> TmdbCollection | None`.
  - Module constants `DISCOVER_TTL = 3 * 3600`, `COLLECTION_TTL = 7 * 24 * 3600`.

**Behaviour to implement** (verified against the live API):
- `movie()` and `tv()` request `append_to_response='external_ids,credits,videos,recommendations'` and `include_video_language=<language>,en,null`. Genres come from `genres[].id`; countries from `production_countries[].iso_3166_1` (TV: `origin_country`, falling back to `production_countries`); cast is the first 10 of `credits.cast` by `order`, photo `w185`; videos are the `results` whose `site` is `YouTube`; recommendations are the `recommendations.results` mapped by the same function as search hits (extract the loop body of `search()` into one helper used by both, and set `rating` from `vote_average` when `vote_count` is positive); the movie's `belongs_to_collection` becomes the ref.
- `discover` calls `/discover/movie` or `/discover/tv` with `language`, `include_adult=false`, `page`, `sort_by` (movie: `popularity.desc`, `primary_release_date.desc`, `title.asc`, `vote_average.desc`; TV: `popularity.desc`, `first_air_date.desc`, `name.asc`, `vote_average.desc`; the rating sort adds `vote_count.gte=100`), `with_genres=tmdb_genre_filter(genre)` when the slug is known, `with_origin_country=<code>`, and `primary_release_year` (movie) or `first_air_date_year` (TV). Hits carry `rating` and `anime_like` as in search. `total` is `total_results`; `has_more` is `page < total_pages`. TTL `DISCOVER_TTL`. A 404 gives an empty `HitPage`.
- `collection` calls `/collection/<id>`; parts become movie `SearchHit`s sorted by release date with undated parts last; TTL `COLLECTION_TTL`; None on 404.

- [ ] **Step 1: Write the failing tests** with `provider_http` and `httpx.MockTransport`, as the existing `tests/test_tmdb.py` does:
  - `test_movie_carries_the_enrichment`: a canned `/movie/603` body with `genres`, `production_countries`, `credits` (12 cast entries), `videos` (a YouTube trailer, a Vimeo clip) and `belongs_to_collection` gives `genre_ids == (28, 878)`, `countries == ('US',)`, exactly 10 credits in `order`, only the YouTube video, and `collection.tmdb_id == 2344`; the request carried `append_to_response` with `credits`, `videos` and `recommendations`.
  - `test_tv_countries_come_from_origin_country`.
  - `test_recommendations_are_search_hits` (kind, year, rating, `anime_like` for a Japanese animation entry).
  - `test_discover_movie_parameters`: the captured query has `sort_by=vote_average.desc`, `vote_count.gte=100`, `with_genres=878|10765` for slug `sci-fi`, `with_origin_country=FR`, `primary_release_year=2019`, `include_adult=false`, `language=ru-RU`; TV uses `first_air_date_year` and `name.asc` for `title`.
  - `test_discover_page_totals`: `total`, `has_more` true on page 1 of 3 and false on page 3.
  - `test_discover_is_cached_for_three_hours`: two identical calls send one request; the stored TTL is `DISCOVER_TTL`.
  - `test_collection_parts_are_sorted_and_cached`, `test_unknown_collection_is_none`.
- [ ] **Step 2: Run to see them fail:** `python -m pytest tests/test_tmdb.py -q`.
- [ ] **Step 3: Implement** to the interfaces above. Extend `tests/fakes.py` only if a later task needs it (Task 6 does).
- [ ] **Step 4: Run** `python -m pytest tests/test_tmdb.py tests/test_search.py tests/test_sync_tmdb.py -q` and `pre-commit run --all-files`. Expected: all pass (search and sync behave as before).
- [ ] **Step 5: Commit** `feat: read genres, cast, videos, recommendations, discover and collections from TMDB`.

---

### Task 4: AniList client: details and discover

**Files:**
- Modify: `sources/providers/anilist.py`, `tests/fakes.py`
- Test: `tests/test_anilist.py`

**Interfaces:**
- Produces in `providers.anilist`:
  - `AniListCredit(character: str, actor: str, photo_url: str)`.
  - `AniListDetails(genres: tuple[str, ...], country: str, trailer: tuple[str, str] | None, cast: tuple[AniListCredit, ...], recommendations: tuple[SearchHit, ...])` where `trailer` is `(site, id)`.
  - `AniListClient.details(anilist_id: int) -> AniListDetails | None` (None when AniList does not know the id; TTL 24 hours).
  - `AniListClient.discover(*, genre: str | None, country: str | None, year: int | None, sort: str, page: int) -> HitPage`; `genre` is an AniList genre name (the caller maps the slug with `anilist_genre_name`), `sort` one of `popularity date title rating`.
  - Constants `DISCOVER_PAGE_SIZE = 20`, `DISCOVER_TTL = 3 * 3600`.

**Behaviour** (verified live):
- `details` queries `Media(id:, type: ANIME)` for `genres`, `countryOfOrigin`, `trailer { id site }`, `characters(role: MAIN, sort: [ROLE, RELEVANCE], perPage: 10)` with `node { name { full } image { medium } }` and `voiceActors(language: JAPANESE) { name { full } }` (the actor is the first voice actor, empty when none), and `recommendations(sort: RATING_DESC, perPage: 10)` nodes' `mediaRecommendation` (skip nulls, adult entries and formats outside `ANIME_FORMATS`; each becomes an `anilist` `SearchHit` of kind `anime` with `rating = averageScore / 10`).
- `discover` queries `Page(page:, perPage: 20) { pageInfo { total hasNextPage } media(type: ANIME, isAdult: false, format_in: [TV, TV_SHORT, MOVIE, SPECIAL, OVA, ONA], genre:, countryOfOrigin:, startDate_greater: Y0000, startDate_lesser: Y1232, sort: [...]) }` omitting the arguments that are None. Sort values: `POPULARITY_DESC`, `START_DATE_DESC`, `TITLE_ENGLISH`, `SCORE_DESC`. Entries with an ANIME `PREQUEL` relation are dropped (request `relations { edges { relationType node { type } } }`), so only the first entry of a franchise is listed. Hits use `english or romaji` as the title, the cover as poster and `rating = averageScore / 10`. `total` is `pageInfo.total`, `has_more` is `hasNextPage`. Validation uses the existing `_validate`.

- [ ] **Step 1: Write the failing tests** with canned GraphQL bodies:
  - `test_details_parse`: genres, `country == 'JP'`, `trailer == ('youtube', 'tR8YH0G67Rk')`, two credits (character, actor, photo), one recommendation hit with `rating == 8.4`; an adult recommendation, a null recommendation and a MANGA-format one are dropped.
  - `test_details_without_voice_actor_has_an_empty_actor`.
  - `test_unknown_entry_has_no_details` (`Media: null` gives None).
  - `test_discover_variables`: the captured body has `genre: 'Sports'`, `countryOfOrigin: 'JP'`, `startDate_greater: 20220000`, `startDate_lesser: 20221232` and `sort: ['SCORE_DESC']`; with no filters those keys are absent.
  - `test_discover_drops_sequels_and_adult`: an entry with a `PREQUEL` edge is dropped; a `SEQUEL` edge is kept.
  - `test_discover_paging`: `total` and `has_more` from `pageInfo`.
  - `test_discover_bad_body_is_an_error`: `{'errors': [...]}` raises `ProviderError`.
- [ ] **Step 2: Run to see them fail:** `python -m pytest tests/test_anilist.py -q`.
- [ ] **Step 3: Implement.** Add `FakeAniList.details(anilist_id)` (reads a `details` dict passed to the constructor, default None) to `tests/fakes.py`.
- [ ] **Step 4: Run** `python -m pytest tests/test_anilist.py tests/test_sync_anime.py tests/test_franchise.py -q` and `pre-commit run --all-files`.
- [ ] **Step 5: Commit** `feat: read details and discover pages from AniList`.

---

### Task 5: The Browse provider layer

**Files:**
- Create: `sources/providers/browse.py`
- Test: `tests/test_browse_providers.py`

**Interfaces:**
- Consumes: `TmdbClient.discover`, `AniListClient.discover`, `catalog.vocab` (`anilist_genre_name`, `ANILIST_COUNTRIES`).
- Produces in `providers.browse`:
  - `SORTS = ('popularity', 'date', 'title', 'rating')`, `MAX_PAGE = 200`.
  - `@dataclass(frozen=True) class BrowseQuery: kind: str | None = None; genre: str | None = None; country: str | None = None; year: int | None = None; sort: str = 'popularity'; page: int = 1` and the classmethod `BrowseQuery.parse(params: Mapping[str, str]) -> BrowseQuery` for untrusted input.
  - `@dataclass class BrowseResults: hits: list[SearchHit]; total: int; has_more: bool; failed: list[str]`.
  - `browse(query: BrowseQuery, language: str, *, tmdb: TmdbClient | None, anilist: AniListClient) -> BrowseResults`.

**Behaviour.**
- `BrowseQuery.parse` takes the first value of each key and keeps it only when valid: `kind` in `movie tv anime`; `genre` in `GENRES`; `country` matches `[A-Z]{2}` and is in `BROWSE_COUNTRIES`; `year` is 4 digits in `1950..current year + 1`; `sort` in `SORTS` (else `popularity`); `page` is a positive integer clamped to `MAX_PAGE` (else 1).
- Sources: `kind` None queries `movie`, `tv` and `anime`; otherwise the one named. `anime` is skipped (not failed) when `country` is set and not in `ANILIST_COUNTRIES`, or when `genre` is set and `anilist_genre_name(genre)` is None. TMDB hits with `anime_like` are dropped. A source that raises `ProviderError`, or TMDB being None, adds its name (`tmdb`, `anilist`) to `failed` once; the others still answer. Hits are interleaved round-robin across the sources (`zip_longest`, as search does); `total` is the sum of the sources' totals, `has_more` is true when any source has more.

- [ ] **Step 1: Write the failing tests** with small fake clients that record their arguments:
  - `test_parse_accepts_valid_values` and `test_parse_ignores_invalid_values` (`year=abc`, `year=1800`, `genre=<script>`, `country=ZZ`, `country=jp`, `sort=x`, `kind=music`, `page=0`, `page=-3`, `page=abc`).
  - `test_browse_query_clamps_the_page`: `page=99999999` gives `MAX_PAGE`.
  - `test_all_kinds_query_three_sources_and_interleave`: the hit order is movie, tv, anime, movie, tv, anime ...
  - `test_a_single_kind_queries_one_source`.
  - `test_anime_is_skipped_for_a_country_it_does_not_cover`: `kind=None, country='FR'` never calls AniList and does not list `anilist` as failed.
  - `test_anime_is_skipped_for_a_genre_it_lacks`: `genre='western'`.
  - `test_japanese_animation_from_tmdb_is_dropped`.
  - `test_one_source_down`: AniList raising leaves TMDB hits and `failed == ['anilist']`; both raising gives no hits and both names.
  - `test_no_tmdb_token_is_a_failed_source`.
  - `test_totals_add_up`: `total` sums, `has_more` ors.
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement `providers/browse.py`.**
- [ ] **Step 4: Run** the tests and `pre-commit run --all-files`.
- [ ] **Step 5: Commit** `feat: merge TMDB and AniList discover into one Browse query`.

---

### Task 6: The sync fills `TitleDetails`

**Files:**
- Modify: `sources/catalog/sync/common.py`, `sources/catalog/sync/movies.py`, `sources/catalog/sync/tv.py`, `sources/catalog/sync/anime.py`, `tests/fakes.py`
- Test: `tests/test_sync_tmdb.py`, `tests/test_sync_anime.py`

**Interfaces:**
- Consumes: Tasks 1 to 4.
- Produces in `catalog.sync.common`:
  - `TRAILER_KEY = re.compile(r'[A-Za-z0-9_-]{6,20}')`, `MAX_TRAILERS = 8`.
  - `trailer_dicts(videos: Iterable[TmdbVideo]) -> list[dict]`: YouTube only, valid key, official first, then Trailer before Teaser before Clip, at most `MAX_TRAILERS`, as the `TitleDetails.trailers` entries.
  - `similar_dicts(hits: Iterable[SearchHit], exclude: Collection[tuple[str, str]] = ()) -> list[dict]`: the `TitleDetails.similar` entries, at most 12, without hits whose `(provider, external_id)` is in `exclude` and without `anime_like` hits.
  - `write_details(title: Title, **fields) -> TitleDetails`: `update_or_create` of the row with exactly the given fields.
- `tests/fakes.py`: `tmdb_movie(...)` and `tmdb_show(...)` accept the new TMDB fields as keyword arguments.

**Behaviour.** `sync_movie` and `sync_tv` write `genres=genres_from_tmdb(item.genre_ids)`, `countries=list(item.countries)`, `cast` (name, role=character, photo_url), `trailers=trailer_dicts(item.videos)`, `similar=similar_dicts(item.recommendations)` and, for movies, `collection` (`{'id', 'name', 'poster_url'}` or None) inside the sync's transaction. `sync_anime` calls `clients.anilist.details(first_main.anilist_id)` after the franchise is loaded: genres via `genres_from_anilist`, `countries=[country]` when set, `cast` (name=actor, role=character, photo_url of the character) skipping credits without an actor, `trailers` from `(site, id)` when the site is `youtube` and the key is valid, `similar=similar_dicts(recs, exclude={('anilist', str(id)) for every id in franchise.own})`. A `ProviderError` from `details` is logged as a warning and leaves the existing row as it was; the sync still succeeds. A `None` result writes empty lists.

- [ ] **Step 1: Write the failing tests.**
  - `test_trailer_dicts_order_and_filter`: a Vimeo video, a bad key (`'a b'`), an unofficial Trailer, an official Teaser and an official Trailer come out as official Trailer, official Teaser, unofficial Trailer; nine valid ones are cut to 8.
  - `test_similar_dicts_excludes_and_caps`: 15 hits give 12; an excluded id and an `anime_like` hit are gone.
  - `test_movie_sync_writes_details` (genres, country, 10 cast entries, a trailer, a collection, similar), `test_tv_sync_writes_details`, `test_resync_replaces_details` (second sync with fewer cast entries leaves exactly those).
  - `test_anime_sync_writes_details_from_the_first_main_entry` (the fake AniList `details` is asked for the first main entry only; own franchise ids are excluded from `similar`).
  - `test_anime_details_failure_keeps_the_old_row`: `details` raising leaves the previous `TitleDetails` untouched and the title synced.
  - `test_anime_without_a_youtube_trailer_has_none`.
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python -m pytest tests/test_sync_tmdb.py tests/test_sync_anime.py tests/test_catalog_tasks.py tests/test_tracking_adding.py -q` and `pre-commit run --all-files`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: store genres, countries, cast, trailers, collection and similar titles on sync`.

---

### Task 7: The open action

**Files:**
- Modify: `sources/catalog/tasks.py`, `sources/tracking/adding.py` (`_discard_placeholder` becomes `discard_placeholder`, callers and tests updated), `sources/web/urls.py`
- Create: `sources/web/views/opening.py`, `sources/web/templates/web/opening.html`
- Test: `tests/test_open.py`

**Interfaces:**
- Consumes: `catalog.sync.get_or_create_title`, `sync_title`, `default_clients`, `catalog.tasks.claim`, `claimed`, `tracking.adding.PROVIDER_KIND`, `discard_placeholder`.
- Produces:
  - `catalog.tasks.open_key(provider: str, external_id: str) -> str` (`open:<provider>:<id>`), `OPEN_CLAIM_TTL = 600`.
  - `catalog.tasks.open_title_task(provider: str, external_id: str) -> str` (a `db_task`; returns `done`, `failed` or `missing`): builds a placeholder `SearchHit` as `add_title_task` does, runs `get_or_create_title` and `sync_title`, discards a placeholder it created when the provider fails, and always releases the claim at the end.
  - `catalog.tasks.enqueue_open_once(provider: str, external_id: str) -> bool` (uses `queue_once`, so a Redis outage costs a job, not the page).
  - URL `open/<str:provider>/<int:external_id>/`, name `open_title`; view `web.views.opening.open_title(request, provider, external_id)`.

**Behaviour.** `open_title` (GET only, `Cache-Control: no-store`): a provider outside `PROVIDER_KIND` is 404; the id is normalised to `str(int(...))` and must be at most 10 digits. If an `ExternalId` with that provider and id exists, answer 302 to `title` (an HTMX poll gets `HX-Redirect` and an empty 200). Otherwise call `enqueue_open_once` and render `opening.html`: a loading state that polls the same URL every 2 seconds. While `claimed(open_key(...))` the poll answers with the loading fragment; once the claim is gone and there is still no title it answers with the error state ("We couldn't open this title", text "The metadata provider didn't answer. Nothing was lost.", a Try again link to the same URL). A Redis outage while enqueueing renders the error state at once.

- [ ] **Step 1: Write the failing tests** (`tests/test_open.py`, using the `fake_clients` fixture and a recorder for the queue as `queued` does):
  - `test_open_redirects_to_an_existing_title` (even a never-synced one).
  - `test_open_enqueues_once`: two GETs queue one job.
  - `test_open_rejects_unknown_providers_and_bad_ids` (404 for `imdb`, 404 for a path with letters, no job queued).
  - `test_open_task_creates_and_syncs_without_a_library_entry`: after the job `ExternalId` exists, `title.synced_at` is set, `LibraryEntry` count is 0, the claim is released.
  - `test_failed_open_leaves_no_placeholder` (use `failing_clients`): no `Title`, no `ExternalId`, claim released, result `failed`.
  - `test_open_task_for_an_anime_entry_of_a_known_franchise_reuses_the_title`.
  - `test_poll_redirects_when_the_title_appears` and `test_poll_reports_failure_when_the_claim_is_gone` (HTMX request headers).
  - `test_open_survives_a_redis_outage` (error state, status 200).
  - `test_open_requires_a_signed_in_user`.
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement.** Add the Russian strings.
- [ ] **Step 4: Run** `python -m pytest tests/test_open.py tests/test_tracking_adding.py tests/test_tracking_scoping.py -q` and `pre-commit run --all-files`.
- [ ] **Step 5: Commit** `feat: open a title nobody has added yet through a background job`.

---

### Task 8: The Browse screen

**Files:**
- Create: `sources/web/views/hits.py`, `sources/web/views/browse.py`, `sources/web/templates/web/browse.html`, `_browse_results.html`, `_browse_card.html`
- Modify: `sources/web/views/search.py` (use `hits.py`), `sources/web/urls.py`, `sources/web/static/web/app.css`, `sources/locale/ru/LC_MESSAGES/django.po`
- Test: `tests/test_web_browse.py`, `tests/test_web_search.py` (unchanged behaviour)

**Interfaces:**
- Consumes: `providers.browse` (Task 5), `catalog.vocab`, `web.clients.shared_clients`, `web.views.opening` URL name `open_title`.
- Produces: `web.views.hits.ResultRow` (moved verbatim from `search.py`, plus `href: str`: the title page URL when the title is in the catalog, else the open URL) and `hits.decorate(user, hits: list[SearchHit]) -> list[ResultRow]` (the former `_rows`, with `_pending_requests`); `web.views.browse.browse_page(request) -> HttpResponse`; URL `browse/`, name `browse`.

**Behaviour.**
- The view parses `request.GET` with `BrowseQuery.parse`, calls `browse(...)` with `request.user.profile.metadata_language` and `shared_clients()`; a `RedisError` is treated as every source failed. A fragment request (`HX-Request` without `HX-History-Restore-Request`; reuse the check from `search.py`) returns only `_browse_results.html`; `Vary` as in search.
- Page: heading is the active filters joined by ` · ` (type as Shows / Anime / Movies, genre, country name in the interface language, year) or "All titles"; count `N titles` (hidden while loading or on error); one `<form method="get">` with hx-get/hx-push-url over the results (pattern of `library.html`): chips of the active filters, each removing its own key (a plain link, so it works without JS); an "Add filter" disclosure with one `<select>` per missing facet (type, genre, country, year; options from `GENRES`, `BROWSE_COUNTRIES`, `browse_years`) that submits on change; a sort select (Popularity, Release date, Title, Rating); "Clear all" when any filter is set.
- Results: a grid of cards (`_browse_card.html`: poster via the poster component with the hit's rating, a link to `row.href`, name, `year · kind`, and the add control exactly as search rows render it: status label when in the library, the progress control when pending, else the picker posting to `search_add`). "Load more" is the last `<li>` of the grid, a link to the next page that targets itself with `hx-swap="outerHTML"`; the response for `page > 1` is the next cards followed by a new "Load more" item when `has_more`. "Showing N of TOTAL" under the grid. States: loading skeleton (`hx-indicator`), error when every queried source failed ("Couldn't load titles", "TMDB and AniList didn't respond. Your filters are kept." with Try again = the same URL), partial notice ("Results may be incomplete: <names> did not respond"), empty ("No titles match these filters", "Remove a filter, or start over." with Clear filters).
- Names use the hit's title, falling back to the original title, then "Untitled".

- [ ] **Step 1: Write the failing tests** (`tests/test_web_browse.py`, patching `web.views.browse.browse` and `shared_clients` like `patched_search` does):
  - `test_browse_renders_cards_with_links`: a known title links to its page, an unknown one to `/open/tmdb_movie/<id>/`.
  - `test_browse_shows_library_state_per_user`: Alice's `Completed` entry shows the label for Alice and an add picker for Bob.
  - `test_browse_heading_and_chips`: `?type=tv&genre=drama&country=IS&year=2025` gives heading `Shows · Drama · Iceland · 2025` and four chips, each linking to the URL without its key; Russian shows `Сериалы · Драма · Исландия · 2025`.
  - `test_browse_passes_the_query_and_language`: the recorder saw `BrowseQuery(kind='tv', genre='drama', ...)` and the user's metadata language.
  - `test_browse_ignores_invalid_parameters` (`year=abc&genre=<script>&country=ZZ&sort=x&page=99999999`): 200, the query the recorder saw is the default clamped one, the markup contains no unescaped `<script>`.
  - `test_browse_with_one_source_down`: partial notice names the provider; `test_browse_error_keeps_the_filters`: every source failed gives the error state with the filter chips and a retry link with the same query string; `test_browse_survives_a_redis_outage` (the recorder raises `RedisError`): 200 error state.
  - `test_browse_empty_state_offers_to_clear_filters`.
  - `test_load_more_returns_only_the_next_cards`: `page=2` with HTMX headers has no `<html>`, ends with a load-more item only when `has_more`, and its link has `page=3`.
  - `test_fragment_request_returns_only_the_results`; `test_browse_query_count_is_constant` (decorating 3 hits and 30 hits runs the same number of queries).
  - `test_search_still_works_after_the_move`: the existing `tests/test_web_search.py` passes unchanged.
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement.** Move `ResultRow`, `_pending_requests` and `_rows` from `search.py` into `hits.py` (rename `_rows` to `decorate`, add `href`), update `search.py` and its imports, then build the view, templates, styles (`mt-` prefix, touch targets at least 44 px, light and dark) and Russian strings.
- [ ] **Step 4: Run** `python -m pytest tests/test_web_browse.py tests/test_web_search.py tests/test_tracking_scoping.py -q`, `pre-commit run --all-files`.
- [ ] **Step 5: Commit** `feat: add the Browse screen`.

---

### Task 9: Title page enrichment

**Files:**
- Modify: `sources/web/views/titles.py`, `sources/web/templates/web/_title_header.html`, `_title_body.html`, `sources/web/templatetags/web_extras.py`, `sources/web/static/web/app.css`, `sources/locale/ru/LC_MESSAGES/django.po`
- Create: `_title_facts.html`, `_see_also.html`, `_cast.html`, `_trailers.html`, `_collection_card.html`, `_similar.html` under `sources/web/templates/web/`
- Test: `tests/test_web_enrichment.py`, `tests/test_web_title.py`

**Interfaces:**
- Consumes: `TitleDetails` (Task 2), `catalog.vocab`, URL names `browse`, `collection`, `open_title`.
- Produces: template filters `genre_label(slug)` and `country_label(code)` (the latter uses the active interface language); a function `web.views.titles.enrichment(title: Title, language: str) -> dict` returning the context keys `details` (the row or None), `genre_links`, `country_links`, `see_also`, `trailers`, `cast`, `cast_more`, `collection`, `similar`; `page_context` merges it when the title is ready.

**Behaviour.**
- Header tags: the genres and countries of the title as links to `browse?type=<kind>&genre=<slug>` and `browse?type=<kind>&country=<code>`. Kind to type: `movie` to `movie`, `tv` to `tv`, `anime` to `anime`.
- "See also": up to 6 distinct links on the title's type, built in this order from the first genre `g1`, the second `g2`, the first country `c1` and the release year `y`: `(g1)`, `(c1)`, `(g1, c1)`, `(g1, y)`, `(g2)`, `(y)`; links whose parts are missing are skipped; each reads like the Browse heading (for example `Shows · Drama · Iceland`). The section is hidden when there are fewer than 2 links.
- Cast: the first 8 shown, the rest behind an Alpine "Show all (N)" toggle (`aria-expanded`); photo or an initials placeholder; the heading is "Voice cast" for anime and "Cast" otherwise.
- Trailers: up to 8 cards. The card is a tinted placeholder with a Play button (`aria-label="Play <name>"`) and the name; the click inserts `<iframe src="https://www.youtube-nocookie.com/embed/<key>?autoplay=1" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen referrerpolicy="strict-origin-when-cross-origin">`; a Stop link removes it. Trailers whose key does not match `TRAILER_KEY` are not rendered. Order: trailers in the user's metadata language first (stable otherwise).
- Collection card (movies): poster, name, link to `collection`. Hidden without a collection.
- Similar: a horizontal scroller of up to 12 cards (poster, name, original title, rating); the link is the title page when the pair `(provider, external_id)` has an `ExternalId` (one query for all items), else `open_title`; a known title shows its name through `localized_title` (translations prefetched in one query).
- Section order: Description, See also, Cast, episodes or the movie block, Trailers, Collections, Similar. A missing `TitleDetails` row or an empty list hides only that section.

- [ ] **Step 1: Write the failing tests** (`tests/test_web_enrichment.py`, `synced_movie` and `synced_show` fixtures plus `TitleDetails` rows):
  - `test_header_links_genres_and_countries_into_browse` (hrefs, Russian label for `drama`, `country_label('JP')` in both languages).
  - `test_see_also_links_and_limit`: with two genres, a country and a year the six links appear in the stated order with the right query strings; with one genre and nothing else the section is absent.
  - `test_cast_shows_eight_and_hides_the_rest_behind_a_toggle` and `test_anime_cast_heading_is_voice_cast`.
  - `test_trailers_load_nothing_before_the_click`: the HTML has no `<iframe` and no `ytimg`, and each card carries its key only in a data attribute.
  - `test_a_bad_trailer_key_is_not_rendered`.
  - `test_collection_card_links_to_the_collection_page`.
  - `test_similar_links_to_known_titles_and_open_for_unknown`: a similar item whose id is in the catalog links to `/titles/<pk>/`, another to `/open/tmdb_movie/<id>/`; the known one shows its localized name.
  - `test_title_page_without_details`: no `TitleDetails` row renders the whole page (status 200, description present, none of the new headings).
  - `test_title_page_query_count_is_constant`: a title with 12 similar items runs no more queries than one with 2.
  - `test_enrichment_does_not_leak_between_users` (the user's note and status still come only from `for_user`; the existing note test keeps passing).
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement** the view function, filters, partials, styles and Russian strings. Keep `_title_body.html` the unit that the sync poll replaces: the new sections live inside it.
- [ ] **Step 4: Run** `python -m pytest tests/test_web_enrichment.py tests/test_web_title.py tests/test_web_lazy.py tests/test_web_actions.py -q` and `pre-commit run --all-files`. Expected: all pass; the title page stays under the size budget asserted by `tests/test_web_lazy.py`.
- [ ] **Step 5: Commit** `feat: show genres, cast, trailers, collection and similar titles on the title page`.

---

### Task 10: The Collection screen

**Files:**
- Create: `sources/web/views/collections.py`, `sources/web/templates/web/collection.html`
- Modify: `sources/web/urls.py`, `sources/web/static/web/app.css`, `sources/locale/ru/LC_MESSAGES/django.po`
- Test: `tests/test_web_collection.py`

**Interfaces:**
- Consumes: `TmdbClient.collection` (Task 3), `hits.decorate` (Task 8), `shared_clients`.
- Produces: `web.views.collections.collection_page(request, tmdb_id: int) -> HttpResponse`; URL `collections/<int:tmdb_id>/`, name `collection`.

**Behaviour.** Calls `shared_clients().tmdb.collection(tmdb_id, metadata_language)`. None (unknown id) gives the 404 page; a `ProviderError`, a missing token or a `RedisError` gives the error state ("Couldn't load this collection", Try again; status 200). Otherwise: name, overview, "N films", the progress line "X of N completed" with a progress bar (X = parts whose library entry is Completed for the user), and one row per part in release order: number, poster, name, `year`, the add control as in Browse (via `decorate`), linking to the title page or the open action. A collection with no parts shows the empty state ("This collection is empty"). There is no "Mark all as watched" (Decision 1).

- [ ] **Step 1: Write the failing tests** (patch the shared clients with a fake TMDB that has `collection`):
  - `test_collection_lists_parts_in_release_order`.
  - `test_collection_progress_counts_only_completed_entries_of_the_user` (Alice completed two of four, Bob none: Alice sees `2 of 4 completed`, Bob `0 of 4 completed`).
  - `test_collection_rows_link_to_known_titles_and_to_open_for_unknown`.
  - `test_unknown_collection_is_a_404`; `test_collection_error_state` (`FailingProvider`); `test_empty_collection`.
  - `test_collection_query_count_is_constant`.
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python -m pytest tests/test_web_collection.py tests/test_tracking_scoping.py -q`, `pre-commit run --all-files`.
- [ ] **Step 5: Commit** `feat: add the Collection screen`.

---

### Task 11: Entry points, documentation, translations sweep

**Files:**
- Modify: `sources/web/templates/web/_search_results.html` (idle state), `sources/locale/ru/LC_MESSAGES/django.po`, `README.md`, `docs/superpowers/specs/2026-10-09-mitaroku-requirements-design.md`
- Test: `tests/test_web_search.py`

**Behaviour.**
- The idle state of Search gets one link: "Or browse by genre, country and year" to `browse`.
- README gets a "Browse and title facts" section: the Browse URL parameters, the cache lifetimes, the open action, and that Browse needs a TMDB token (AniList needs none).
- Spec section 5 notes that Browse has no bottom-bar tab; section 13 drops the plan 3b item and records: "Mark all as watched" on the Collection page needs a bulk add beyond the 5-request cap.
- Translation sweep: every string added by Tasks 1 to 10 has a Russian entry; the old `(msgid, msgstr)` set is intact. Verify with `msgfmt --check -o /dev/null sources/locale/ru/LC_MESSAGES/django.po`, with `python sources/manage.py makemessages -l ru --no-location` on a scratch copy of the file to list missing msgids (never overwrite the real file), and by loading Browse, a title page and the Collection page with the language set to Russian and grepping the HTML for leftover English UI strings.

- [ ] **Step 1: Write the failing test** `test_idle_search_links_to_browse`.
- [ ] **Step 2: Run it to see it fail; implement; run it to see it pass.**
- [ ] **Step 3: Do the documentation and the sweep above.**
- [ ] **Step 4: Run the whole suite** (`python -m pytest -q`), `pre-commit run --all-files`, `makemigrations --check --dry-run`.
- [ ] **Step 5: Commit** `docs: describe Browse and the title facts, link Browse from Search`.

---

### Task 12: Live pass and browser pass (controller only)

This task needs the TMDB token, so the controller runs it from their own shell with `MITAROKU_PROVIDERS__TMDB_TOKEN` set in that process's environment; subagents never see the token and never read credential files or process environments. Use a scratch database and Redis db (never the `mitaroku` development database) and drop them afterwards.

- [ ] **Step 1: Live check of the providers.** Browse `type=tv&genre=drama&country=IS`, `type=movie&genre=sci-fi&year=2019&sort=rating`, `type=anime&genre=sports&year=2022`, and no filters at all, in English and Russian. Expected: plausible results, no Japanese animation in the TMDB part, anime listed once per franchise, totals present, the second page differs from the first.
- [ ] **Step 2: Live check of the sync.** Sync The Matrix (603), Breaking Bad (1396) and Frieren (AniList 154587). Expected: genres, countries, cast, trailers and similar titles stored; The Matrix has its collection (2344); Frieren's cast lists voice actors and its similar list excludes its own franchise.
- [ ] **Step 3: Browser pass** (headless Chromium, 390 and 960 px, light and dark, English and Russian): Browse with chips and the Add filter disclosure, Load more, the error state (point TMDB at a closed port), a title page with every new section, a trailer click (the iframe appears only then), the Matrix collection page, an unknown title opened through a Browse card (opening page, then the syncing title page, then content), and the "See also" links. Check horizontal overflow at 390 px and that every touch target is at least 44 px.
- [ ] **Step 4: Record the findings** in the ledger; fix Critical and Important ones in one pass with a failing test first; list the Minor ones.
- [ ] **Step 5: Final whole-branch review** (most capable model), one fix pass, one scoped re-review, then the PR.
