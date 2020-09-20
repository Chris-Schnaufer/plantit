from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from plantit.users.models import Profile


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'Profile'
    fk_name = 'user'


class UserProfileAdmin(UserAdmin):
    inlines = (ProfileInline,)

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return list()
        return super(UserProfileAdmin, self).get_inline_instances(request, obj)


admin.site.unregister(User)
admin.site.register(User, UserProfileAdmin)


from django.contrib import admin

from plantit.targets.models import Target


class TargetInline(admin.StackedInline):
    model = Target
    can_delete = True


class TargetAdmin(admin.ModelAdmin):
    inlines = (TargetInline,)

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return list()
        return super(TargetAdmin, self).get_inline_instances(request, obj)


admin.site.unregister(Target)
admin.site.register(Target, TargetAdmin)