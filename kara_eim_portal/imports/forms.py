from django import forms

from .models import DatasetImport


class DatasetUploadForm(forms.ModelForm):
    class Meta:
        model = DatasetImport
        fields = ["title", "jurisdiction", "reporting_year", "framework_version", "uploaded_file"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Example: EIM field inventory - October 2026"}),
            "jurisdiction": forms.TextInput(attrs={"placeholder": "Minnesota, National, or multi-jurisdiction"}),
            "reporting_year": forms.NumberInput(attrs={"min": 1990, "max": 2100}),
            "framework_version": forms.TextInput(attrs={"placeholder": "Example: 1.2"}),
            "uploaded_file": forms.ClearableFileInput(attrs={"accept": ".xlsx"}),
        }

    def clean_uploaded_file(self):
        uploaded = self.cleaned_data["uploaded_file"]
        if uploaded.size > 25 * 1024 * 1024:
            raise forms.ValidationError("The workbook must be 25 MB or smaller.")
        if not uploaded.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Upload an Excel .xlsx workbook.")
        return uploaded


class ReviewForm(forms.Form):
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Optional review notes"}),
    )
