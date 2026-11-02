# Form Models { #form-models }

You can use **Pydantic models** to declare **form fields** in FastAPI.

/// note

To use forms, first install [`python-multipart`](https://github.com/Kludex/python-multipart).

Add it to your project:

```console
$ uv add python-multipart
```

///

/// note

This is supported since FastAPI version `0.113.0`. 🤓

///

## Pydantic Models for Forms { #pydantic-models-for-forms }

You just need to declare a **Pydantic model** with the fields you want to receive as **form fields**, and then declare the parameter as `Form`:

```python
from typing import Annotated

from fastapi import FastAPI, Form
from pydantic import BaseModel

app = FastAPI()


class FormData(BaseModel):
    username: str
    password: str


@app.post("/login/")
async def login(data: Annotated[FormData, Form()]):
    return data
```

**FastAPI** will **extract** the data for **each field** from the **form data** in the request and give you the Pydantic model you defined.

For [HTML checkboxes](request-forms.md#boolean-form-fields), declare boolean fields with a default of `False`.

## Check the Docs { #check-the-docs }

You can verify it in the docs UI at `/docs`:

<div class="screenshot">
<img src="/img/tutorial/request-form-models/image01.png">
</div>

## Forbid Extra Form Fields { #forbid-extra-form-fields }

In some special use cases (probably not very common), you might want to **restrict** the form fields to only those declared in the Pydantic model. And **forbid** any **extra** fields.

/// note

This is supported since FastAPI version `0.114.0`. 🤓

///

You can use Pydantic's model configuration to `forbid` any `extra` fields:

```python
from typing import Annotated

from fastapi import FastAPI, Form
from pydantic import BaseModel

app = FastAPI()


class FormData(BaseModel):
    username: str
    password: str
    model_config = {"extra": "forbid"}


@app.post("/login/")
async def login(data: Annotated[FormData, Form()]):
    return data
```

If a client tries to send some extra data, they will receive an **error** response.

For example, if the client tries to send the form fields:

* `username`: `Rick`
* `password`: `Portal Gun`
* `extra`: `Mr. Poopybutthole`

They will receive an error response telling them that the field `extra` is not allowed:

```json
{
    "detail": [
        {
            "type": "extra_forbidden",
            "loc": ["body", "extra"],
            "msg": "Extra inputs are not permitted",
            "input": "Mr. Poopybutthole"
        }
    ]
}
```

## Combine Form Models { #combine-form-models }

You can combine multiple form models with individual `Form` parameters.

```python
from typing import Annotated

from fastapi import FastAPI, Form
from pydantic import BaseModel

app = FastAPI()


class Profile(BaseModel):
    name: str


class Preferences(BaseModel):
    tags: list[str] = []


@app.post("/profiles/")
async def create_profile(
    profile: Annotated[Profile, Form()],
    preferences: Annotated[Preferences, Form()],
    token: Annotated[str, Form()],
):
    return {"profile": profile, "preferences": preferences, "token": token}
```

The client sends `name`, `tags`, and `token` as form fields. Each model validates the form fields independently.

Each model's `extra` configuration applies to all form fields, including fields and files declared in other parameters or dependencies. With `extra="forbid"`, any field outside that model is rejected. With `extra="allow"`, those fields are included as model extras.

If multiple declarations use the same form field name, each validates that field's value. Use compatible types and constraints for shared fields so the OpenAPI schema can describe them consistently.

## Optional Form Models { #optional-form-models }

Declare a form model with `| None` and a default of `None` to make the group optional.

```python
from typing import Annotated

from fastapi import FastAPI, Form
from pydantic import BaseModel

app = FastAPI()


class Profile(BaseModel):
    name: str
    age: int


@app.post("/profiles/")
async def create_profile(
    token: Annotated[str, Form()],
    profile: Annotated[Profile | None, Form()] = None,
):
    return {"token": token, "profile": profile}
```

If neither `name` nor `age` is sent, `profile` is `None`. If either is sent, FastAPI validates the model and requires both fields. An empty submitted value also counts as supplying a field.

Using `| None` without a default still makes the parameter required.

Extra fields follow the model's `extra` configuration. Ignored extras do not make the model present.

An optional model containing only unchecked checkboxes is omitted. Send a value such as `enabled=false` to supply it explicitly.

## Optional Unions of Form Models { #optional-unions-of-form-models }

You can also use an optional union of models.

```python
from typing import Annotated, Literal

from fastapi import FastAPI, Form
from pydantic import BaseModel

app = FastAPI()


class Person(BaseModel):
    kind: Literal["person"]
    name: str


class Company(BaseModel):
    kind: Literal["company"]
    company_name: str


@app.post("/contacts/")
async def create_contact(
    contact: Annotated[Person | Company | None, Form(discriminator="kind")] = None,
):
    return contact
```

If no form fields are sent, `contact` is `None`. Send `kind=person` with `name`, or `kind=company` with `company_name`. The discriminator selects which model to validate.

## Combine Unions of Form Models { #combine-unions-of-form-models }

You can combine unions with other form models, individual `Form` parameters, and `File` parameters. Each union is validated independently.

```python
from typing import Annotated, Literal

from fastapi import FastAPI, Form
from pydantic import BaseModel

app = FastAPI()


class Person(BaseModel):
    contact_type: Literal["person"]
    name: str


class Company(BaseModel):
    contact_type: Literal["company"]
    company_name: str


class Email(BaseModel):
    notification_type: Literal["email"]
    email: str


class SMS(BaseModel):
    notification_type: Literal["sms"]
    phone: str


@app.post("/contacts/")
async def create_contact(
    contact: Annotated[Person | Company, Form(discriminator="contact_type")],
    token: Annotated[str, Form()],
    notification: Annotated[Email | SMS | None, Form(discriminator="notification_type")] = None,
):
    return {"contact": contact, "notification": notification, "token": token}
```

Send all fields in the same form. For example, send `contact_type=person`, `name=Rick`, `notification_type=email`, `email=rick@example.com`, and `token=secret`.

Omit the notification fields to leave `notification` as `None`.

## Form Models and Files { #form-models-and-files }

You can also combine a form model with [file parameters](request-files.md).

```python
from typing import Annotated

from fastapi import FastAPI, Form, UploadFile
from pydantic import BaseModel

app = FastAPI()


class Profile(BaseModel):
    name: str


@app.post("/profiles/")
async def create_profile(profile: Annotated[Profile, Form()], avatar: UploadFile):
    return {"profile": profile, "filename": avatar.filename}
```

Here the client sends the `name` field and the `avatar` file using `multipart/form-data`.

## Summary { #summary }

You can use Pydantic models to declare form fields in FastAPI. 😎
