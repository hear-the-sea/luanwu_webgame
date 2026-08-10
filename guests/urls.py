from django.urls import path

from .views.equipment import equip_view, gear_options_view, unequip_view
from .views.items import use_medicine_item_view
from .views.recruit import RecruitView, accept_candidate_view, use_magnifying_glass_view, use_recruitment_card_view
from .views.roster import GuestDetailView, RosterView, dismiss_guest_view
from .views.salary import pay_all_salaries_view, pay_salary_view
from .views.skills import forget_skill_view, learn_skill_view
from .views.training import TrainView, allocate_points_view, check_training_view, use_experience_item_view

app_name = "guests"

urlpatterns = [
    path("", RosterView.as_view(), name="roster"),
    path("<int:pk>/", GuestDetailView.as_view(), name="detail"),
    path("<int:pk>/dismiss/", dismiss_guest_view, name="dismiss"),
    path("<int:pk>/learn-skill/", learn_skill_view, name="learn_skill"),
    path("<int:pk>/forget-skill/", forget_skill_view, name="forget_skill"),
    path("<int:pk>/allocate-points/", allocate_points_view, name="allocate_points"),
    path("<int:pk>/use-exp-item/", use_experience_item_view, name="use_exp_item"),
    path("<int:pk>/use-medicine/", use_medicine_item_view, name="use_medicine_item"),
    path("<int:pk>/pay-salary/", pay_salary_view, name="pay_salary"),
    path("<int:pk>/check-training/", check_training_view, name="check_training"),
    path("recruit/", RecruitView.as_view(), name="recruit"),
    path("recruit/use-card/", use_recruitment_card_view, name="use_recruitment_card"),
    path("train/", TrainView.as_view(), name="train"),
    path("equip/", equip_view, name="equip"),
    path("gear-options/", gear_options_view, name="gear_options"),
    path("unequip/", unequip_view, name="unequip"),
    path("candidates/accept/", accept_candidate_view, name="candidate_accept"),
    path("candidates/reveal/", use_magnifying_glass_view, name="use_magnifying_glass"),
    path("pay-all-salaries/", pay_all_salaries_view, name="pay_all_salaries"),
]
