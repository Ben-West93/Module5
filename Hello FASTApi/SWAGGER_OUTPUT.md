# Swagger UI Output (copy-paste)

Captured from http://127.0.0.1:8000/docs with `uvicorn main:app --reload` running.

## Hello FastAPI 1.0.0 — endpoints listed in Swagger UI

```
GET   /                    Root
GET   /hello               Say Hello
GET   /info                Get Info
GET   /about               About
GET   /greet/{name}        Greet
POST  /echo                Echo
GET   /items               List Items
GET   /items/{item_id}     Get Item
```

## Try it out — responses

`GET /` → 200
```json
{"message": "Welcome to my first API"}
```
`GET /about` → 200
```json
{"name": "Ben", "module": "Module 5 — FastAPI Development", "fun_fact": "I live in Iowa, where there are more pigs than people."}
```
`GET /greet/Ben` → 200
```json
{"greeting": "Hello, Ben! Welcome to my first API."}
```
`POST /echo` body `{"message": "hi there", "shout": true}` → 200
```json
{"message": "HI THERE", "shout": true}
```

## Validation error — `POST /echo` with `shout` as a string

Body: `{"message": "hi", "shout": "maybe"}` → **422 Unprocessable Entity**
```json
{"detail": [{"type": "bool_parsing", "loc": ["body", "shout"], "msg": "Input should be a valid boolean, unable to interpret input", "input": "maybe"}]}
```

FastAPI/Pydantic rejected the request before `echo()` ran; `loc` pinpoints the bad field.
