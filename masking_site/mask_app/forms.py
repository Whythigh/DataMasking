from django import forms

class UploadFileForm(forms.Form):
    file = forms.FileField(label="Select a CSV or Excel or XML file")

class ColumnSelectForm(forms.Form):
    def __init__(self, *args, **kwargs):
        columns = kwargs.pop('columns', [])
        super().__init__(*args, **kwargs)
        self.fields['columns'] = forms.MultipleChoiceField(
            choices=[(col, col) for col in columns],
            widget=forms.CheckboxSelectMultiple,
            label="Select columns to mask",
            required=False  # or True if you want at least one selection
        )


class EmailForm(forms.Form):
    subject = forms.CharField(max_length=100, label="Subject")
    message = forms.CharField(widget=forms.Textarea, label="Message")
    recipient = forms.EmailField(label="Recipient Email")

