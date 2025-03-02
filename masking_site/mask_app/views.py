from django.shortcuts import render

# Create your views here.
import pandas as pd
import random, string
from django.http import HttpResponse
from .forms import UploadFileForm, ColumnSelectForm, EmailForm
from django.core.mail import send_mail
from django.shortcuts import render, redirect
from django.conf import settings


# Dictionary to store masked values to ensure consistent mapping
masked_values = {}

def mask_value(value):
    """Mask a value by changing two letters, two digits, or adjusting dates."""
    if value in masked_values:
        return masked_values[value]

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

    masked_values[value] = new_value
    return new_value

def upload_file(request):
    """
    Handles file upload and renders a column selection form after reading the file.
    """
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
                    # Requires pandas >= 1.3.0 and lxml
                    df = pd.read_xml(file)
                else:
                    return HttpResponse("Unsupported file format. Please upload a CSV or Excel or XML file.")
            except Exception as e:
                return HttpResponse(f"Error reading file: {e}")

            # Save the dataframe in the session as JSON (for simplicity; note: large files may require a different approach)
            request.session['dataframe'] = df.to_json()

            # Render column selection form using the dataframe's columns
            form = ColumnSelectForm(columns=df.columns)
            return render(request, 'mask_app/select_columns.html', {'form': form})
    else:
        form = UploadFileForm()
    return render(request, 'mask_app/upload.html', {'form': form})

def mask_columns(request):
    if request.method == 'POST':
        df_json = request.session.get('dataframe')
        if not df_json:
            return HttpResponse("No file data found in session. Please try again.")
        df = pd.read_json(df_json)

        selected_columns = request.POST.getlist('columns')
        if not selected_columns:
            return HttpResponse("No columns selected. Please select at least one column.")

        try:
            for col in selected_columns:
                # e.g. approach_name = approach_customerID
                approach_key = f"approach_{col}"
                approach = request.POST.get(approach_key, 'random')  # default to random if missing

                if approach == 'xxx':
                    # Replace entire column with "XXX"
                    df[col] = "XXX"
                else:
                    # Use your existing random logic
                    df[col] = df[col].apply(mask_value)

        except Exception as e:
            return HttpResponse(f"Error during masking: {e}")

        # Prepare the masked data as CSV for download
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="masked_data.csv"'
        df.to_csv(response, index=False)
        return response

    return HttpResponse("Invalid request method.")



# my_app/views.py


def contact_view(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        user_email = request.POST.get('email')
        user_message = request.POST.get('message')

        # Construct subject/body
        subject = f"New message from {name}"
        body = (
            f"Name: {name}\n"
            f"Email: {user_email}\n"
            f"Message:\n{user_message}"
        )

        # Adjust 'recipient@example.com' to wherever you want to receive the messages
        recipient_list = ['recipient@example.com']

        try:
            send_mail(
                subject,
                body,
                settings.DEFAULT_FROM_EMAIL,  # or a specific "from" email
                recipient_list,
                fail_silently=False
            )
            return HttpResponse("Email sent successfully!")
        except Exception as e:
            return HttpResponse(f"Error sending email: {str(e)}")

    # If GET request or something else, just redirect or show a page
    return redirect('home')  # or wherever you want


