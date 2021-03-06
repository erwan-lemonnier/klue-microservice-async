from pymacaron.log import pymlogger
from pymacaron_core.models import PyMacaronModel
from pymacaron_core.models import get_model


log = pymlogger(__name__)


def model_to_task_arg(o):
    """Take an object, and if it is a PyMacaron model, serialize it into json so it
    can be passed as argument of a Celery task. Otherwise return the object
    unchanged. If it's a list, serialize its items in the same way.
    """

    if isinstance(o, PyMacaronModel):
        # It's a PyMacaron model, let's serialize it to json and attach a magic marker to it
        j = o.to_json()
        j['__pymacaron_model_name'] = o.get_model_name()
        log.debug("Serialized PyMacaronModel %s to celery task arg" % j['__pymacaron_model_name'])
        return j
    elif type(o) is list:
        return [model_to_task_arg(oo) for oo in o]
    else:
        return o


def task_arg_to_model(o):
    """The opposite of model_to_tupple"""
    if type(o) is dict and '__pymacaron_model_name' in o:
        model_name = o['__pymacaron_model_name']
        log.debug("Deserialized celery task arg to PyMacaronModel: %s" % model_name)
        del o['__pymacaron_model_name']
        return get_model(model_name).from_json(o)
    elif type(o) is list:
        return [task_arg_to_model(oo) for oo in o]
    else:
        return o
