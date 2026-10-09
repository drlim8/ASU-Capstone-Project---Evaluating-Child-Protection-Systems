from django.urls import path

from . import views


app_name = "imports"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("upload/", views.upload_dataset, name="upload"),
    path("imports/<int:pk>/", views.import_detail, name="detail"),
    path("imports/<int:pk>/revalidate/", views.revalidate_import, name="revalidate"),
    path("imports/<int:pk>/approve/", views.approve_import, name="approve"),
    path("imports/<int:pk>/reject/", views.reject_import, name="reject"),
    path("imports/<int:pk>/validation-report/", views.download_validation_report, name="validation_report"),
    path("published/", views.published_data, name="published"),
]
