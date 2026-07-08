from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.core.mail import send_mail
from django.conf import settings
from .forms import UploadFileForm, ColumnSelectForm
import pandas as pd
import random
import string
import io
import base64
import re
import hashlib
from rest_framework.decorators import api_view
from rest_framework.response import Response


# ── Core masking function ───────────────────────────────────────

def mask_value(value, mapping):
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
            joined = ''.join(value_list)
            new_value = float(joined) if '.' in value_str else int(joined)
        else:
            new_value = value
    elif isinstance(value, pd.Timestamp):
        new_value = value + pd.DateOffset(days=random.randint(1, 365))
    else:
        new_value = value

    mapping[value] = new_value
    return new_value


# ── Web pages ───────────────────────────────────────────────────

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

        # build summary
        summary_rows = []
        for col in df.columns:
            if col in selected_columns:
                summary_rows.append({'Column': col, 'Status': 'MASKED', 'Reason': 'selected by user'})
            else:
                summary_rows.append({'Column': col, 'Status': 'unchanged', 'Reason': 'not selected'})
        summary_df = pd.DataFrame(summary_rows)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Masked Data')
            summary_df.to_excel(writer, index=False, sheet_name='DataRepli Summary')
        output.seek(0)

        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="masked_data.xlsx"'
        return response

    return HttpResponse("Invalid request method.")


def contact_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        user_email = request.POST.get('email')
        user_message = request.POST.get('message')
        subject = f"New message from {name}"
        body = f"Name: {name}\nEmail: {user_email}\nMessage:\n{user_message}"
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL,
                      ['daniilforsteam@gmail.com'], fail_silently=False)
            return HttpResponse("Message sent!")
        except Exception as e:
            return HttpResponse(f"Error: {str(e)}")
    return redirect('home')


# ── PII auto-detection ──────────────────────────────────────────

PII_PATTERNS = {
    'name':    ['name', 'nimi', 'namn', 'first_name', 'last_name',
                'full_name', 'firstname', 'lastname', 'eesnimi', 'perenimi',
                'forename', 'surname'],
    'email':   ['email', 'e-mail', 'meil', 'epost', 'sahkoposti'],
    'phone':   ['phone', 'tel', 'mobile', 'telefon', 'puhelin',
                'mobil', 'gsm', 'cell'],
    'iban':    ['iban', 'account', 'konto', 'kontonummer', 'tili',
                'bank_account', 'account_number'],
    'ssn':     ['ssn', 'isikukood', 'personal_code', 'id_number',
                'henkilotunnus', 'personnummer', 'national_id'],
    'address': ['address', 'aadress', 'adress', 'street', 'city',
                'osoite', 'postinumero', 'zipcode', 'postal'],
    'dob':     ['dob', 'birth', 'syntyma', 'birthday',
                'date_of_birth', 'birthdate'],
}

def scan_values_for_pii(series):
    sample = series.dropna().astype(str).head(30)
    for val in sample:
        if re.match(r'[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}', val):
            return 'iban'
        if re.match(r'[\w.\-]+@[\w.\-]+\.\w{2,}', val):
            return 'email'
        if re.match(r'\+\d{7,14}', val):
            return 'phone'
        if re.match(r'\d{6}[-+A]\d{3}[A-Z0-9]', val):
            return 'ssn'
    return None

def auto_detect_pii(df):
    rules = {}
    for col in df.columns:
        col_lower = str(col).lower().strip().replace(' ', '_')
        for field_type, patterns in PII_PATTERNS.items():
            if any(p in col_lower for p in patterns):
                rules[col] = 'mask'
                break
        if col not in rules:
            detected = scan_values_for_pii(df[col])
            if detected:
                rules[col] = 'mask'
    return rules


# ── File masking API endpoint ───────────────────────────────────

@api_view(['POST'])
def mask_file_api(request):
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return Response({'error': 'Missing API key'}, status=401)

    raw_key = auth[7:]
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    from .models import ApiKey, UsageLog
    try:
        key_record = ApiKey.objects.get(key_hash=key_hash, active=True)
    except ApiKey.DoesNotExist:
        return Response({'error': 'Invalid API key'}, status=401)

    file_b64  = request.data.get('file_base64')
    file_type = request.data.get('file_type', 'xlsx').lower()
    auto      = request.data.get('auto_detect', True)
    rules     = request.data.get('rules', {})

    if not file_b64:
        return Response({'error': 'No file_base64 provided'}, status=400)

    try:
        file_bytes = base64.b64decode(file_b64)
        file_obj   = io.BytesIO(file_bytes)
        if file_type == 'csv':
            df = pd.read_csv(file_obj)
        else:
            df = pd.read_excel(file_obj)
    except Exception as e:
        return Response({'error': f'Could not read file: {e}'}, status=400)

    force_mask = request.data.get('force_mask', [])
    never_mask = request.data.get('never_mask', [])

    if auto:
        rules = auto_detect_pii(df)

    for col in force_mask:
        if col in df.columns:
            rules[col] = 'mask'
    for col in never_mask:
        rules.pop(col, None)

    mapping = {}
    masked_fields = list(rules.keys())

    for col, strategy in rules.items():
        if col not in df.columns:
            continue
        if strategy == 'redact':
            df[col] = '***'
        elif strategy == 'mask':
            df[col] = df[col].apply(lambda v: mask_value(v, mapping))

    output = io.BytesIO()
    if file_type == 'csv':
        df.to_csv(output, index=False)
        mime = 'text/csv'
    else:
        summary_rows = []
        for col in df.columns:
            if col in masked_fields:
                summary_rows.append({'Column': col, 'Status': 'MASKED', 'Reason': 'PII detected'})
            else:
                summary_rows.append({'Column': col, 'Status': 'unchanged', 'Reason': 'no PII detected'})
        summary_df = pd.DataFrame(summary_rows)

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Masked Data')
            summary_df.to_excel(writer, index=False, sheet_name='DataRepli Summary')
        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    output.seek(0)
    encoded = base64.b64encode(output.read()).decode('utf-8')

    UsageLog.objects.create(
        api_key=key_record,
        rows_processed=len(df),
        fields_masked=masked_fields
    )

    return Response({
        'status':         'done',
        'rows_processed': len(df),
        'fields_masked':  masked_fields,
        'file_type':      file_type,
        'mime_type':      mime,
        'file_base64':    encoded,
    })