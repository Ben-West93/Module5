# Validated Contact Book API (L3)

Pydantic v2 schemas with layered validation, wired into a FastAPI router.

## Layout

```
validated-contacts/
├── app/
│   ├── main.py                 # FastAPI app, includes the router
│   ├── routers/contacts.py     # the four endpoints + in-memory storage
│   └── schemas/contact.py      # ContactCategory, ContactCreate, ContactUpdate, ContactResponse
├── tests/test_contacts.py      # 21 tests covering happy paths, 422s, and edge cases
├── conftest.py                 # puts the project root on sys.path for pytest
└── requirements.txt
```

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Swagger UI: http://localhost:8000/docs

```bash
pytest
```

## Endpoints

| Method | Path             | Body / Query        | Notes                        |
|--------|------------------|---------------------|------------------------------|
| POST   | `/contacts/`     | `ContactCreate`     | 201, assigns `id`/`created_at` |
| GET    | `/contacts/`     | `?category=work`    | filter is optional           |
| GET    | `/contacts/{id}` | —                   | 404 if missing               |
| PATCH  | `/contacts/{id}` | `ContactUpdate`     | only sent fields change      |

## Things to try in Swagger

Each of these should come back 422 with a readable `detail` array:

| Input                          | Why it fails                          |
|--------------------------------|---------------------------------------|
| `"email": "no-at-sign"`        | custom `field_validator`              |
| `"category": "frenemy"`        | not a member of `ContactCategory`     |
| `"phone": "123"`               | under `min_length=10`                 |
| `"first_name": ""`             | under `min_length=1`                  |
| `"first_name": "   "`          | blank after strip (see note below)    |
| `"first_name"` 51 chars        | over `max_length=50`                  |
| `email` key omitted            | required field missing                |

Note the two error shapes. `Field()` constraints produce Pydantic's built-in
message ("String should have at least 10 characters"); a custom validator
produces "Value error, ..." with whatever text you raised.

## Notes on the implementation

**`exclude_unset` is not `exclude_none`.** `model_dump(exclude_unset=True)`
drops keys the client never sent — but a key sent explicitly as `null` counts as
set. Writing that `None` into `first_name` produced a 500 on serialization *and*
left a corrupted record that made every later read of that contact fail too. The
router now honors `null` only for fields in `NULLABLE_FIELDS` (just `phone`),
so `{"phone": null}` clears the phone and `{"first_name": null}` is ignored.

**Validation on the update path.** `ContactUpdate` repeats the email and
blank-name validators rather than only making fields optional. Without them,
create is validated and PATCH silently isn't — `PATCH {"email": "broken"}` would
return 200 and store it. Each validator starts with `if v is None: return v`,
since an optional field still runs its validator when the key is present.

**`min_length=1` counts characters, not content.** `"   "` is length 3 and
passes, so `name_must_not_be_blank` strips and re-checks.

**`ContactResponse` inherits from `ContactCreate`** instead of redeclaring the
fields, so response validation can't drift from create validation when a field
is added later. `ConfigDict(from_attributes=True)` is groundwork for L5 — it
allows `ContactResponse.model_validate(orm_object)` instead of hand-building a
dict.

**Trailing slashes.** The skeleton registers routes at `"/"` under a
`/contacts` prefix, so the real paths are `/contacts/`. A request to
`/contacts` gets a 307 redirect to it. Harmless in the browser and in Swagger;
if you'd rather have clean paths, change the decorators from `"/"` to `""`.

## Known limits (deliberate, given the exercise scope)

- **Storage is a module-level list.** It resets on restart, and it isn't safe
  under concurrent writes — two simultaneous POSTs could read the same
  `_next_id`. Fine for one process in dev; a database handles this in L5.
- **Phone accepts any 10–15 characters**, so `"abcdefghij"` passes. The spec
  says characters, not digits. If you want digits only, add a validator that
  strips `+`, spaces, `-`, `(`, `)` and then checks `.isdigit()`.
- **Email validation is the `"@"` check the exercise asked for**, which accepts
  plenty of invalid addresses. Real projects use `EmailStr` from
  `pydantic[email]`.
- **No DELETE endpoint** — not in the spec.

## Additions beyond the skeleton

Worth being able to explain if this gets graded against a reference solution:

1. Blank-name validators (`"   "` would otherwise pass).
2. Validators on `ContactUpdate`, not just optional fields.
3. Duplicate email returns 409, compared case-insensitively. A contact book
   with two Adas is a data problem, and the check skips the contact being
   updated so re-sending your own email isn't a self-collision.
4. `reset_storage()` so tests don't leak state between cases.
