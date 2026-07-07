from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.core.mail import send_mail
from django.conf import settings
from .forms import UploadFileForm, ColumnSelectForm
import pandas as pd
import io
import random
import string

# FIX 1: move masked_values inside functions — global dict is shared across all users
def mask_value(value, mapping):
    """Pass mapping dict per-request, not global."""
    if value in mapping:
        return mapping[value]

    if isinstance(value, str) and len(value) > 1:
        value_list = list(value)
        letter_indices = [i for i, c in enumerate(value_list) if c.isalpha()]
        if len(letter_indices) >= 2:
            idx1, idx2 = random.sample(letter_indices, 2)
            value_list[idx1] = random.choice(string.ascii_letters)
            value_list[idx2] = random.choice(string.ascii_letters)
        new_value = ''.join(value_list)
    elif isinstance(value, (int, float)):
        value_str = str(value)
        digit_indices = [i for i, c in enumerate(value_str) if c.isdigit()]
        if len(digit_indices) >= 2:
            idx1, idx2 = random.sample(digit_indices, 2)
            value_list = list(value_str)
            value_list[idx1] = random.choice(string.digits)
            value_list[idx2] = random.choice(string.digits)
            new_value = float(''.join(value_list)) if '.' in value_str else int(''.join(value_list))
        else:
            new_value = value
    elif isinstance(value, pd.Timestamp):
        new_value = value + pd.DateOffset(days=random.randint(1, 365))
    else:
        new_value = value

    mapping[value] = new_value
    return new_value

# FIX 2: add home view
def home(request):
    return render(request, 'index.html')

def upload_file(request):
    if request.method == 'POST':
        form = UploadFileForm(request.POST, request.FILES)
        if form.is_valid():
            file = request.FILES['file']
            try:
                if file.name.endswith('.csv'):
                    df = pd.read_csv(file)
                elif file.name.endswith(('.xls', '.xlsx')):
                    df = pd.read_excel(file)
                elif file.name.endswith('.xml'):
                    df = pd.read_xml(file)
                else:
                    return HttpResponse("Unsupported file format.")
            except Exception as e:
                return HttpResponse(f"Error reading file: {e}")

            request.session['dataframe'] = df.to_json()
            form = ColumnSelectForm(columns=df.columns)
            return render(request, 'mask_app/select_columns.html', {'form': form})
    else:
        form = UploadFileForm()
    return render(request, 'mask_app/upload.html', {'form': form})

def mask_columns(request):
    if request.method == 'POST':
        df_json = request.session.get('dataframe')
        if not df_json:
            return HttpResponse("Session expired. Please upload your file again.")
        df = pd.read_json(io.StringIO(df_json))
        selected_columns = request.POST.getlist('columns')
        if not selected_columns:
            return HttpResponse("No columns selected.")

        # FIX 3: per-request mapping dict instead of global
        mapping = {}
        try:
            for col in selected_columns:
                approach = request.POST.get(f"approach_{col}", 'fpe')
                if approach == 'xxx':
                    df[col] = "XXX"
                else:
                    df[col] = df[col].apply(lambda v: mask_value(v, mapping))
        except Exception as e:
            return HttpResponse(f"Error during masking: {e}")

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="masked_data.csv"'
        df.to_csv(response, index=False)
        return response

    return HttpResponse("Invalid request method.")

def contact_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        user_email = request.POST.get('email')
        user_message = request.POST.get('message')
        subject = f"New message from {name}"
        body = f"Name: {name}\nEmail: {user_email}\nMessage:\n{user_message}"

        # FIX 4: update recipient to your real email
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL,
                      ['daniilforsteam@gmail.com'], fail_silently=False)
            return HttpResponse("Message sent!")
        except Exception as e:
            return HttpResponse(f"Error: {str(e)}")
    return redirect('home')