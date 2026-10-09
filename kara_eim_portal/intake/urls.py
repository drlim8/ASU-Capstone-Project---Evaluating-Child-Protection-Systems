from django.urls import path

from . import views

app_name = "intake"

urlpatterns = [
    path("", views.home, name="home"),
    path("new/", views.batch_new, name="batch_new"),
    path("<int:pk>/", views.batch_detail, name="batch_detail"),
    path("<int:pk>/status.json", views.batch_status, name="batch_status"),
    path("doc/<int:pk>/retry/", views.document_retry, name="document_retry"),
    path("doc/<int:pk>/remove/", views.document_remove, name="document_remove"),
]
