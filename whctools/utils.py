from django.utils import timezone
import datetime

from allianceauth.services.hooks import get_extension_logger

from app_utils.logging import LoggerAddTag

from whctools import __title__
from whctools.models import Acl, ACLHistory, Applications, ApplicationHistory
from allianceauth.notifications import notify
from .app_settings import TRANSIENT_REJECT

# 3.0 backwards compatibility
from allianceauth.eveonline.models import EveCharacter
from django.contrib.auth.models import User
from allianceauth.authentication.models import CharacterOwnership

logger = LoggerAddTag(get_extension_logger(__name__), __title__)


def remove_character_from_community(app,new_state, reason, reject_time):
    """
    remove a singular character application to a new_state for a given reason with a reject_time cooldown on a new application
    """
    try:
        
        app.member_state = new_state
        app.reject_reason = reason
        app.reject_timeout = timezone.now() + datetime.timedelta(
                    days=int(reject_time)
                )
        app.save()
        
    except:
        logger.error(f"Failed to remove character from community app: {app} for reason {reason}")

def remove_all_alts(acl_name, member_application, new_state, reason, reject_time):
    """
    Remove all alts from the acl and set their applications to the new state
    """
    all_characters = get_all_related_characters_from_character(member_application.eve_character)
    for char in all_characters:
        logger.debug(f"[Remove All Alts]- checking alt {char.character_name} for application")
        app = Applications.objects.filter(eve_character=char)[0]
        if app:
            logger.debug(f"Removing alt named {app.eve_character.character_name} belonging to {member_application.eve_character.character_name}")
            old_state = app.member_state
            remove_character_from_community(app, new_state, reason, reject_time)
            remove_character_from_acl(app.eve_character.character_id, acl_name, old_state, app.member_state, reason)

                 
    notification_names = ", ".join([char.character_name for char in all_characters])
    return notification_names

def remove_character_from_acl(char_id, acl_name, from_state, to_state, reason):
    """Helper function to remove a character from an acl"""


    acl_object = Acl.objects.filter(pk=acl_name)
    if acl_object:
        characters = acl_object[0].characters.all()
        for char in characters:
            if char.character_id == char_id:
                logger.debug(f"Removing {char.character_name} form {acl_name} - setting to {Applications.MembershipStates(to_state).name} for {reason.name}")
                acl_object[0].characters.remove(char)
                history_entry = ACLHistory(
                    character=char,
                    date_of_change=timezone.now(),
                    old_state = from_state,
                    new_state = to_state, 
                    reason_for_change = reason,
                    changed_by = "ToDo",
                    acl=acl_object[0]
                )
                history_entry.save()
                acl_object[0].changes.add(history_entry)
                return

def add_character_to_acl(acl_name, eve_character, old_state, new_state, reason):
    logger.debug(f"Adding {eve_character.character_name} to {acl_name} - setting to {Applications.MembershipStates(new_state).name} for reason of {reason.name}")
    acl_obj = Acl.objects.get(pk=acl_name)
    if acl_obj:
        acl_obj.characters.add(eve_character)
        history_entry = ACLHistory(
                character=eve_character,
                date_of_change=timezone.now(),
                old_state=old_state,
                new_state=new_state,
                reason_for_change=reason,
                changed_by="Acceptance (todo)",
                acl=acl_obj
            )
        history_entry.save()
        acl_obj.changes.add(history_entry)


def log_application_change(application:Applications, old_state=Applications.MembershipStates.NOTAMEMBER, reason=Applications.RejectionStates.NONE):
    log_user_application_change = ApplicationHistory(
        application=application,
        old_state=old_state ,
        new_state=application.member_state,
        reject_reason=reason
    )
    log_user_application_change.save()



def update_all_acls_for_character_leaving_alliance(character, acls_character_is_on, character_acl_application, user, existing_acl_state):
    """
    Removes a character from all ACls they may be on, and informs the user for each character that they have been removed due to leaving the uni.
    """

    if character_acl_application is not None:
        remove_character_from_community(character_acl_application. Applications.MembershipStates.REJECTED,Applications.RejectionStates.LEFT_ALLIANCE, TRANSIENT_REJECT)
        new_state = Applications.MembershipStates.REJECTED
    else:
        new_state = Applications.MembershipStates.NOTAMEMBER

    for acl in acls_character_is_on:
        logger.debug(f"Removing {character.character_name} from {acl.name}")
        remove_character_from_acl(character.character_id, acl.name, existing_acl_state, new_state, ACLHistory.ApplicationStateChangeReason.LEFT_UNI )

        notify.danger(
            user,
            "WHC Community Status",
            f"Your status with {acl.name} on {character.character_name} has been removed\n:Reason: Character is no longer part of IVY or IVY-A",
        )

def remove_in_process_application(user, application_details):
    """
    For a character that has an un-accepted application still open, withdraw it.
    """
    try:
        application_details.member_state = Applications.MembershipStates.REJECTED
        application_details.reject_reason = Applications.RejectionStates.LEFT_ALLIANCE
        application_details.save()
                        
        log_application_change(
            application=application_details,
            old_state=Applications.MembershipStates.APPLIED)
                        
        notify.danger(
            user,
            "WHC Application",
            f"Your character {application_details.eve_character.character_name} has left IVY or IVY-A and so your application to join WHC has been rejected automatically.",
        ) 
    except:
        logger.error(f"Could not remove in process application of {application_details} for user: {user}" )


def get_all_related_characters_from_character(character:EveCharacter):
    """ helper function for getting all the characters/alts of a particular character"""
    user_obj = bc_get_user_from_eve_character(character)
    return bc_get_all_characters_from_user(user_obj)

def bc_get_main_character_name_from_user(user:User):
    """
    3.0 Backwards compatible version of framework.api.user.get_main_character_name_from_user
    """
    if user is None:
        return None

    try:
        main_character = user.profile.main_character
    except AttributeError:
        return None

    return main_character
    
def bc_get_all_characters_from_user(user:User):
    """
    3.0 Backwards compatible version of framework.api.user.get_all_characters_from_user
    """
    if user is None:
        return []

    try:
        characters = [
            char.character for char in CharacterOwnership.objects.filter(user=user)
        ]
    except AttributeError:
        return []

    return characters

def bc_get_user_from_eve_character(character:EveCharacter):
    """
    3.0 Backwards compatible version of framework.api.evecharacter import get_user_from_evecharacter
    """
    try:
        userprofile = character.character_ownership.user.profile
    except (
        AttributeError,
        EveCharacter.character_ownership.RelatedObjectDoesNotExist,
        CharacterOwnership.user.RelatedObjectDoesNotExist,
    ):
        # replaces get_sentinel_user wrapper from 4.0
        # basically getting a user object of some kind to return, even if its an 'empty' one
        return User.objects.get_or_create(username="deleted")[0]


    return userprofile.user


def bc_get_main_character_from_evecharacter(character:EveCharacter):
    """
    3.0 Backwards compatible version of framework.api.evecharacter import get_main_character_from_evecharacter
    """
    try:
        userprofile = character.character_ownership.user.profile
    except (
        AttributeError,
        EveCharacter.character_ownership.RelatedObjectDoesNotExist,
        CharacterOwnership.user.RelatedObjectDoesNotExist,
    ):
        return None

    return userprofile.main_character
