# Cookie Parameter Models { #cookie-parameter-models }

If you have a group of **cookies** that are related, you can create a **Pydantic model** to declare them. 🍪

This would allow you to **re-use the model** in **multiple places** and also to declare validations and metadata for all the parameters at once. 😎

/// note

This is supported since FastAPI version `0.115.0`. 🤓

///

/// tip

This same technique applies to `Query`, `Cookie`, and `Header`. 😎

///

## Cookies with a Pydantic Model { #cookies-with-a-pydantic-model }

Declare the **cookie** parameters that you need in a **Pydantic model**, and then declare the parameter as `Cookie`:

```python
from typing import Annotated

from fastapi import Cookie, FastAPI
from pydantic import BaseModel

app = FastAPI()


class Cookies(BaseModel):
    session_id: str
    fatebook_tracker: str | None = None
    googall_tracker: str | None = None


@app.get("/items/")
async def read_items(cookies: Annotated[Cookies, Cookie()]):
    return cookies
```

**FastAPI** will **extract** the data for **each field** from the **cookies** received in the request and give you the Pydantic model you defined.

## Check the Docs { #check-the-docs }

You can see the defined cookies in the docs UI at `/docs`:

<div class="screenshot">
<img src="/img/tutorial/cookie-param-models/image01.png">
</div>

/// note

Have in mind that, as **browsers handle cookies** in special ways and behind the scenes, they **don't** easily allow **JavaScript** to touch them.

If you go to the **API docs UI** at `/docs` you will be able to see the **documentation** for cookies for your *path operations*.

But even if you **fill the data** and click "Execute", because the docs UI works with **JavaScript**, the cookies won't be sent, and you will see an **error** message as if you didn't write any values.

///

## Forbid Extra Cookies { #forbid-extra-cookies }

In some special use cases (probably not very common), you might want to **restrict** the cookies that you want to receive.

Your API now has the power to control its own cookie consent. 🤪🍪

You can use Pydantic's model configuration to `forbid` any `extra` fields:

```python
from typing import Annotated

from fastapi import Cookie, FastAPI
from pydantic import BaseModel

app = FastAPI()


class Cookies(BaseModel):
    model_config = {"extra": "forbid"}

    session_id: str
    fatebook_tracker: str | None = None
    googall_tracker: str | None = None


@app.get("/items/")
async def read_items(cookies: Annotated[Cookies, Cookie()]):
    return cookies
```

If a client tries to send some **extra cookies**, they will receive an **error** response.

Poor cookie banners with all their effort to get your consent for the API to reject it. 🍪

For example, if the client tries to send a `santa_tracker` cookie with a value of `good-list-please`, the client will receive an **error** response telling them that the `santa_tracker` cookie is not allowed:

```json
{
    "detail": [
        {
            "type": "extra_forbidden",
            "loc": ["cookie", "santa_tracker"],
            "msg": "Extra inputs are not permitted",
            "input": "good-list-please",
        }
    ]
}
```

## Combine Parameter Models { #combine-parameter-models }

You can combine multiple cookie parameter models with individual cookie parameters and dependencies. Each model is extracted and validated independently, as explained for [query parameter models](query-param-models.md#combine-parameter-models).

Each model's `extra` configuration applies to all cookie parameters, including those declared in other models or dependencies. With `extra="forbid"`, any cookie outside that model is rejected. With `extra="allow"`, those cookies are included as model extras.

## Optional Cookie Parameter Models { #optional-cookie-parameter-models }

Declare a cookie parameter model with `| None` and a default of `None` to make the group optional.

```python
from typing import Annotated

from fastapi import Cookie, FastAPI
from pydantic import BaseModel

app = FastAPI()


class Preferences(BaseModel):
    theme: str
    locale: str


@app.get("/items/")
async def read_items(preferences: Annotated[Preferences | None, Cookie()] = None):
    return preferences
```

If neither `theme` nor `locale` is sent, `preferences` is `None`. If either is sent, FastAPI validates the model and requires both fields.

The same rules for defaults, extra fields, and OpenAPI apply as for [optional query parameter models](query-param-models.md#optional-query-parameter-models).

## Summary { #summary }

You can use **Pydantic models** to declare **cookies** in **FastAPI**. 😎
