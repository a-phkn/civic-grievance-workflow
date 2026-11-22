"""All forms. Every validator mirrors a rule in the database or the
normalization domains, so bad input is stopped with a clear message before
it reaches PostgreSQL. The database still enforces the same rules (CHECKs,
triggers) as the last line of defence.

Every FlaskForm carries a hidden CSRF token automatically (Flask-WTF).
"""
import re

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileSize
from wtforms import (IntegerField, PasswordField, SelectField, StringField,
                     SubmitField, TextAreaField)
from wtforms.validators import (DataRequired, Email, EqualTo, InputRequired, Length,
                                NumberRange, Optional, Regexp, ValidationError)

NAME_RULE = Regexp(r"^[A-Za-z][A-Za-z .'-]*$",
                   message="Use letters, spaces, dots, apostrophes or hyphens only.")


def strong_password(_form, field):
    pw = field.data or ""
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        raise ValidationError("Use at least one letter and one number.")


def lower_strip(value):
    return value.strip().lower() if isinstance(value, str) else value


def strip(value):
    return value.strip() if isinstance(value, str) else value


# ------------------------------------------------------------- DFD 1.0 / login
class LoginForm(FlaskForm):
    email = StringField("Email", filters=[lower_strip],
                        validators=[DataRequired("Enter your email."), Email("Enter a valid email address."),
                                    Length(max=255, message="Use at most 255 characters.")])
    password = PasswordField("Password", validators=[DataRequired("Enter your password."),
                                                     Length(max=128, message="Use at most 128 characters.")])
    submit = SubmitField("Log in")


class RegisterForm(FlaskForm):
    """Citizen self-registration (DFD 1.0, 'registration details').
    The role is never taken from the form: the server always sets CITIZEN."""
    name = StringField("Full name", filters=[strip],
                       validators=[DataRequired("Enter your name."), Length(2, 100, "Use 2 to 100 characters."), NAME_RULE])
    email = StringField("Email", filters=[lower_strip],
                        validators=[DataRequired("Enter your email."), Email("Enter a valid email address."),
                                    Length(max=255, message="Use at most 255 characters.")])
    password = PasswordField("Password", validators=[DataRequired("Choose a password."),
                                                     Length(8, 128, "Use 8 to 128 characters."),
                                                     strong_password])
    confirm = PasswordField("Confirm password",
                            validators=[DataRequired("Type the password again."),
                                        EqualTo("password", "The two passwords do not match.")])
    submit = SubmitField("Create account")


# ------------------------------------------------------------- DFD 2.0
class ComplaintForm(FlaskForm):
    description = TextAreaField("What is the problem, and where?", filters=[strip],
                                validators=[DataRequired("Describe the problem."),
                                            Length(20, 2000, "Use 20 to 2,000 characters.")])
    photo = FileField("Photo (optional)", validators=[
        FileAllowed(["jpg", "jpeg", "png"], "Only JPG or PNG images are accepted."),
        FileSize(max_size=5 * 1024 * 1024, message="The photo must be 5 MB or smaller.")])
    submit = SubmitField("Submit complaint")


# ------------------------------------------------------------- DFD 5.2
class TaskStatusForm(FlaskForm):
    """Choices are filled per task with only the legal next statuses."""
    new_status = SelectField("New status", validators=[DataRequired("Choose the next step.")], choices=[])
    submit = SubmitField("Update")


# ------------------------------------------------------------- DFD 4.3 / 5.4
class ReasonForm(FlaskForm):
    """Every reviewer decision needs a reason; it goes into AUDIT_LOG."""
    reason = TextAreaField("Reason", filters=[strip],
                           validators=[DataRequired("Give a reason."),
                                       Length(5, 500, "Use 5 to 500 characters.")])
    submit = SubmitField("Confirm")


# ------------------------------------------------------------- DFD 1.0 (admin)
class AgencyForm(FlaskForm):
    agency_name = StringField("Agency name", filters=[strip],
                              validators=[DataRequired("Enter a name."), Length(2, 100, "Use 2 to 100 characters.")])
    description = TextAreaField("Description", filters=[strip],
                                validators=[Optional(), Length(max=500, message="Use at most 500 characters.")])
    submit = SubmitField("Add agency")


class TaskTypeForm(FlaskForm):
    agency_id = SelectField("Agency", coerce=int, validators=[InputRequired("Choose an agency.")])
    task_code = StringField("Task code", filters=[lambda v: v.strip().upper() if isinstance(v, str) else v],
                            validators=[DataRequired("Enter a task code."), Length(2, 30, "Use 2 to 30 characters."),
                                        Regexp(r"^[A-Z][A-Z0-9_]*$",
                                               message="Capital letters, digits and _ only, e.g. PIPE_REPAIR.")])
    task_description = TextAreaField("What the task involves", filters=[strip],
                                     validators=[Optional(), Length(max=500, message="Use at most 500 characters.")])
    sla_hours = IntegerField("SLA (hours)", validators=[
        InputRequired("Enter the SLA in hours."),
        NumberRange(1, 8760, "Use a whole number from 1 to 8760 (one year).")])
    submit = SubmitField("Add task type")


class SlaForm(FlaskForm):
    sla_hours = IntegerField("New SLA (hours)", validators=[
        InputRequired("Enter the SLA in hours."),
        NumberRange(1, 8760, "Use a whole number from 1 to 8760.")])
    submit = SubmitField("Save")


class StaffAccountForm(FlaskForm):
    name = StringField("Full name", filters=[strip],
                       validators=[DataRequired("Enter a name."), Length(2, 100, "Use 2 to 100 characters."), NAME_RULE])
    email = StringField("Email", filters=[lower_strip],
                        validators=[DataRequired("Enter an email."), Email("Enter a valid email address."),
                                    Length(max=255, message="Use at most 255 characters.")])
    role = SelectField("Role", choices=[("AGENCY_STAFF", "Agency staff"), ("REVIEWER", "Reviewer")],
                       validators=[DataRequired("Choose a role.")])
    agency_id = SelectField("Agency (agency staff only)", coerce=int, validators=[Optional()])
    password = PasswordField("Temporary password", validators=[
        DataRequired("Set a temporary password."), Length(8, 128, "Use 8 to 128 characters."),
        strong_password])
    submit = SubmitField("Create account")

    def validate_agency_id(self, field):
        # mirrors CHECK chk_user_agency: agency set exactly when role = AGENCY_STAFF
        if self.role.data == "AGENCY_STAFF" and not field.data:
            raise ValidationError("Agency staff must belong to an agency.")
        if self.role.data != "AGENCY_STAFF" and field.data:
            raise ValidationError("Only agency staff belong to an agency. Leave this empty.")
