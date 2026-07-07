from django.contrib import admin
from django.urls import path
from mask_app.views import upload_file, mask_columns, contact_view, home

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home, name='home'),           # ← landing page
    path('upload/', upload_file, name='upload_file'),
    path('mask/', mask_columns, name='mask_columns'),
    path('contact/', contact_view, name='contact_view')
]