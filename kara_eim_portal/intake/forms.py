from django import forms
from django.conf import settings

from .models import SourceDocument
from .services.batches import parse_urls
from .services.detect import VALID_TYPES_MESSAGE


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)] if data else []


class IntakeBatchForm(forms.Form):
    title = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={"placeholder": "Example: Minnesota annual reports - October 2026"}),
    )
    default_scope = forms.CharField(
        required=False,
        max_length=100,
        label="Default scope",
        widget=forms.TextInput(attrs={"placeholder": "Minnesota, National, or multi-jurisdiction"}),
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    files = MultipleFileField(
        required=False,
        label="Files",
        widget=MultipleFileInput(attrs={"accept": ".pdf,.xlsx"}),
        help_text="PDF or Excel (.xlsx) files.",
    )
    urls = forms.CharField(
        required=False,
        label="URLs",
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "One http(s) URL per line"}),
        help_text="One URL per line, linking to a web page, PDF or Excel file.",
    )

    def clean(self):
        cleaned = super().clean()
        files = cleaned.get("files") or []
        urls = []
        if "urls" not in self.errors:
            try:
                urls = parse_urls(cleaned.get("urls", ""))
            except forms.ValidationError as exc:
                self.add_error("urls", exc)
        cleaned["urls"] = urls
        if "files" in self.errors or "urls" in self.errors:
            return cleaned

        if not files and not urls:
            raise forms.ValidationError("Add at least one file or URL.")
        limit = settings.INTAKE_MAX_ITEMS_PER_BATCH
        if len(files) + len(urls) > limit:
            raise forms.ValidationError(f"A batch can contain at most {limit} items.")
        for f in files:
            name = f.name
            if not name.lower().endswith((".pdf", ".xlsx")):
                self.add_error("files", f"{name}: {VALID_TYPES_MESSAGE}")
            elif f.size > settings.INTAKE_MAX_BYTES:
                mb = settings.INTAKE_MAX_BYTES // (1024 * 1024)
                self.add_error("files", f"{name}: files must be {mb} MB or smaller.")
        return cleaned


class DocumentMetaForm(forms.ModelForm):
    year_hint = forms.IntegerField(
        required=False,
        min_value=1990,
        max_value=2100,
        label="Year hint",
        widget=forms.NumberInput(attrs={"min": 1990, "max": 2100}),
    )

    class Meta:
        model = SourceDocument
        fields = ["scope", "year_hint"]
