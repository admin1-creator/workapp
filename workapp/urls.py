from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.urls import path
from . import views


def _login(view):
    return login_required(view)


urlpatterns = [
    path("login/", LoginView.as_view(template_name="workapp/login.html"), name="login"),
    path("signup/", views.signup_view, name="signup"),
    path("logout/", views.logout_view, name="logout"),

    path("", _login(views.workrecord_search), name="workrecord_search"),
    path("workrecord/search/", _login(views.workrecord_search), name="workrecord_search"),
    path("workrecord/voucher/<int:pk>/", _login(views.workrecord_voucher_detail), name="workrecord_voucher_detail"),
    path("workrecord/create/", _login(views.workrecord_create), name="workrecord_create"),
    path("workrecord/list/", _login(views.workrecord_list), name="workrecord_list"),
    path("workrecord/edit/<int:pk>/", _login(views.workrecord_edit), name="workrecord_edit"),
    path("workrecord/review/", _login(views.workrecord_review), name="workrecord_review"),
    path("workrecord/confirm/", _login(views.workrecord_confirm_update), name="workrecord_confirm_update"),
    path("workrecord/print/period/", _login(views.workrecord_print_period), name="workrecord_print_period"),
    path("workrecord/<int:pk>/print/", _login(views.workrecord_print), name="workrecord_print"),

    path("unit/moto/", _login(views.unit_moto), name="unit_moto"),
    path("unit/shokunin/", _login(views.unit_shokunin), name="unit_shokunin"),
    path("unit/temoto/", _login(views.unit_temoto), name="unit_temoto"),
    path("unit/ouen/", _login(views.unit_ouen), name="unit_ouen"),
    path("delete/<int:pk>/", _login(views.workrecord_delete), name="workrecord_delete"),
    path("monthly/", _login(views.monthly_summary), name="monthly_summary"),
    path("get_unit_price/", _login(views.get_unit_price), name="get_unit_price"),
]
