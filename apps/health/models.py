"""
health: the user's plan and targets, body weight, the link to the metric
trackables (water, sleep, steps, activity) it creates in tracking, and the food
database.

Write through health.services only. Dates are the user's local dates.
"""

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.tracking.models import TimeStampedModel, Trackable

from .calculations import (
    HEIGHT_CM_RANGE,
    WEIGHT_KG_RANGE,
    ActivityLevel,
    Goal,
    Mode,
    Sex,
)
from .text import normalize


class HealthPlan(TimeStampedModel):
    """
    One version of the user's plan: the inputs and the targets computed from them.
    A new row each time the plan changes; a second change on the same day updates
    that day's row. The plan that applied on a date is the latest one with
    effective_from <= date, so changing the plan never changes past dates.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='health_plans')
    effective_from = models.DateField()  # user's local date

    # Inputs (weight lives in WeightEntry)
    goal = models.CharField(max_length=10, choices=Goal.choices)
    sex = models.CharField(max_length=10, choices=Sex.choices)
    birth_year = models.PositiveSmallIntegerField()  # the UI sends an age
    height_cm = models.DecimalField(
        max_digits=4, decimal_places=1,
        validators=[MinValueValidator(HEIGHT_CM_RANGE[0]), MaxValueValidator(HEIGHT_CM_RANGE[1])],
    )
    activity_level = models.CharField(max_length=15, choices=ActivityLevel.choices)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.AUTO)
    water_mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.AUTO)

    # Targets
    calories = models.PositiveIntegerField()
    protein_g = models.PositiveIntegerField()
    carbs_g = models.PositiveIntegerField()
    fat_g = models.PositiveIntegerField()
    water_ml = models.PositiveIntegerField()
    steps = models.PositiveIntegerField()
    activity_minutes = models.PositiveSmallIntegerField()
    sleep_hours = models.DecimalField(max_digits=3, decimal_places=1)
    calorie_floor_applied = models.BooleanField(
        default=False, help_text='The calories were raised to the safety floor (1500 men, 1200 women).',
    )

    class Meta:
        ordering = ['-effective_from']
        constraints = [
            models.UniqueConstraint(fields=['user', 'effective_from'], name='uniq_health_plan_per_day'),
        ]

    def __str__(self):
        return f'{self.user} plan from {self.effective_from}: {self.calories} kcal'


class WeightEntry(TimeStampedModel):
    """Body weight on one local date (one per day). BMI is computed, never stored."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='weights')
    date = models.DateField()
    weight_kg = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(WEIGHT_KG_RANGE[0]), MaxValueValidator(WEIGHT_KG_RANGE[1])],
    )

    class Meta:
        ordering = ['-date']
        constraints = [models.UniqueConstraint(fields=['user', 'date'], name='uniq_weight_per_day')]

    def __str__(self):
        return f'{self.user} on {self.date}: {self.weight_kg} kg'


class HealthMetric(TimeStampedModel):
    """
    The link between a health metric and its trackable in tracking. Health owns it:
    exactly one trackable per metric per user, found through this row (never by name).
    """

    class Metric(models.TextChoices):
        WATER = 'water', 'Water'
        SLEEP = 'sleep', 'Sleep'
        STEPS = 'steps', 'Steps'
        ACTIVITY = 'activity', 'Activity'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='health_metrics')
    metric = models.CharField(max_length=10, choices=Metric.choices)
    trackable = models.OneToOneField(Trackable, on_delete=models.CASCADE, related_name='health_metric')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'metric'], name='uniq_health_metric_per_user')]

    def __str__(self):
        return f'{self.user}: {self.metric}'


# --------------------------------------------------------------------------- #
# Food database. Global foods (user=NULL) are managed by staff and loaded with
# `import_foods`; custom foods (user set) are private to their owner.
# --------------------------------------------------------------------------- #
class PortionUnit(models.TextChoices):
    PLATE = 'plate', 'Plate'
    SMALL_PLATE = 'small_plate', 'Small plate'
    BOWL = 'bowl', 'Bowl'
    PIECE = 'piece', 'Piece'
    SANDWICH = 'sandwich', 'Sandwich'
    SLICE = 'slice', 'Slice'
    LOAF = 'loaf', 'Loaf'
    CUP = 'cup', 'Cup'
    GLASS = 'glass', 'Glass'
    CAN = 'can', 'Can'
    BOTTLE = 'bottle', 'Bottle'
    TBSP = 'tbsp', 'Tablespoon'
    HANDFUL = 'handful', 'Handful'


# Shown by the apps in the user's language. Adding a unit = a new entry here and above.
PORTION_UNIT_LABELS = {
    PortionUnit.PLATE: {'ar': 'طبق', 'en': 'Plate', 'fr': 'Assiette'},
    PortionUnit.SMALL_PLATE: {'ar': 'طبق صغير', 'en': 'Small plate', 'fr': 'Petite assiette'},
    PortionUnit.BOWL: {'ar': 'سلطانية', 'en': 'Bowl', 'fr': 'Bol'},
    PortionUnit.PIECE: {'ar': 'قطعة', 'en': 'Piece', 'fr': 'Pièce'},
    PortionUnit.SANDWICH: {'ar': 'ساندويتش', 'en': 'Sandwich', 'fr': 'Sandwich'},
    PortionUnit.SLICE: {'ar': 'شريحة', 'en': 'Slice', 'fr': 'Tranche'},
    PortionUnit.LOAF: {'ar': 'رغيف', 'en': 'Loaf', 'fr': 'Galette'},
    PortionUnit.CUP: {'ar': 'كوب', 'en': 'Cup', 'fr': 'Tasse'},
    PortionUnit.GLASS: {'ar': 'كوباية', 'en': 'Glass', 'fr': 'Verre'},
    PortionUnit.CAN: {'ar': 'علبة', 'en': 'Can', 'fr': 'Canette'},
    PortionUnit.BOTTLE: {'ar': 'زجاجة', 'en': 'Bottle', 'fr': 'Bouteille'},
    PortionUnit.TBSP: {'ar': 'معلقة كبيرة', 'en': 'Tablespoon', 'fr': 'Cuillère à soupe'},
    PortionUnit.HANDFUL: {'ar': 'حفنة', 'en': 'Handful', 'fr': 'Poignée'},
}

# Accepted values (CLAUDE.md decisions, health round 2)
KCAL_PER_100G_MAX = 900
MACRO_PER_100G_MAX = 100
PORTION_GRAMS_RANGE = (1, 5000)


def _nutrient(max_value, help_text):
    return models.DecimalField(max_digits=5, decimal_places=1, help_text=help_text,
                               validators=[MinValueValidator(0), MaxValueValidator(max_value)])


class Food(TimeStampedModel):
    class Category(models.TextChoices):
        DISH = 'dish', 'Dish'
        DRINK = 'drink', 'Drink'
        FRUIT = 'fruit', 'Fruit'
        VEGETABLE = 'vegetable', 'Vegetable'
        BREAD = 'bread', 'Bread'
        GRAIN = 'grain', 'Grain'
        PROTEIN = 'protein', 'Meat, fish, eggs'
        DAIRY = 'dairy', 'Dairy'
        SWEET = 'sweet', 'Sweet'
        SNACK = 'snack', 'Snack'
        SAUCE = 'sauce', 'Sauce'
        OTHER = 'other', 'Other'

    class Source(models.TextChoices):
        NNI = 'nni', 'NNI (Egypt)'
        USDA = 'usda', 'USDA'
        OFF = 'off', 'Open Food Facts'
        CUSTOM = 'custom', 'Custom'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='custom_foods',
        help_text='Empty: a global food. Set: a private custom food of this user.',
    )
    key = models.SlugField(max_length=100, blank=True,
                           help_text='Identifier in the source dataset; `import_foods` matches on (source, key).')
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.CUSTOM)
    is_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, help_text='Inactive foods are hidden from search.')

    name_ar = models.CharField(max_length=120, blank=True)
    name_en = models.CharField(max_length=120, blank=True)
    name_fr = models.CharField(max_length=120, blank=True)
    aliases = models.JSONField(default=list, blank=True,
                               help_text='Other names, any language: ["فلافل", "ta\'meya"].')
    category = models.CharField(max_length=15, choices=Category.choices, default=Category.OTHER)

    # Per 100 g
    kcal = _nutrient(KCAL_PER_100G_MAX, 'kcal per 100 g')
    protein_g = _nutrient(MACRO_PER_100G_MAX, 'g per 100 g')
    carbs_g = _nutrient(MACRO_PER_100G_MAX, 'g per 100 g')
    fat_g = _nutrient(MACRO_PER_100G_MAX, 'g per 100 g')

    # Normalized names + aliases (health.text.normalize), rebuilt on every save.
    search_text = models.TextField(editable=False, default='')

    class Meta:
        ordering = ['name_en', 'name_ar']
        constraints = [
            models.UniqueConstraint(fields=['source', 'key'], condition=models.Q(user__isnull=True),
                                    name='uniq_global_food_key'),
            models.CheckConstraint(condition=models.Q(user__isnull=True) | models.Q(source='custom'),
                                   name='custom_food_source'),
        ]
        indexes = [
            GinIndex(fields=['search_text'], opclasses=['gin_trgm_ops'], name='food_search_trgm'),
            models.Index(fields=['user', 'is_active']),
        ]

    def __str__(self):
        return self.name_en or self.name_ar or self.name_fr

    @property
    def names(self) -> dict:
        return {'ar': self.name_ar, 'en': self.name_en, 'fr': self.name_fr}

    def clean(self):
        errors = {}
        if not (self.name_ar or self.name_en or self.name_fr):
            errors['name_en'] = 'At least one name (ar, en or fr) is required.'
        if not isinstance(self.aliases, list) or not all(isinstance(a, str) for a in self.aliases):
            errors['aliases'] = 'Must be a list of names.'
        macros = [self.protein_g, self.carbs_g, self.fat_g]
        if all(m is not None for m in macros) and sum(macros) > MACRO_PER_100G_MAX:
            errors['fat_g'] = 'Protein + carbs + fat cannot exceed 100 g per 100 g.'
        if self.user_id is None and not self.key:
            errors['key'] = 'Required for a global food.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        texts = [self.name_ar, self.name_en, self.name_fr, *(self.aliases or [])]
        self.search_text = ' | '.join(t for t in (normalize(x) for x in texts) if t)
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = {*kwargs['update_fields'], 'search_text'}
        super().save(*args, **kwargs)


class FoodPortion(TimeStampedModel):
    """A named portion of a food and its weight: koshari "plate" = 350 g."""

    food = models.ForeignKey(Food, on_delete=models.CASCADE, related_name='portions')
    unit = models.CharField(max_length=15, choices=PortionUnit.choices)
    grams = models.DecimalField(
        max_digits=6, decimal_places=1,
        validators=[MinValueValidator(PORTION_GRAMS_RANGE[0]), MaxValueValidator(PORTION_GRAMS_RANGE[1])],
    )

    class Meta:
        ordering = ['grams']
        constraints = [models.UniqueConstraint(fields=['food', 'unit'], name='uniq_food_portion_unit')]

    def __str__(self):
        return f'{self.food}: {self.unit} = {self.grams} g'

    @property
    def labels(self) -> dict:
        return PORTION_UNIT_LABELS[self.unit]
