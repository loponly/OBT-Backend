import types
from typing import Any, Callable, List, TypeVar
import inspect
from functools import wraps

FuncT = TypeVar("FuncT", bound=Callable[..., Any])

def create_deco_meta(wrappers: List[FuncT]):
    class DecoMeta(type):
        def __new__(cls, name, bases, attrs):
            for attr_name, attr_value in attrs.items():
                if isinstance(attr_value, types.FunctionType):
                    attrs[attr_name] = cls.deco(attr_value)

            return super().__new__(cls, name, bases, attrs)

        @classmethod
        def deco(cls, func: FuncT) -> FuncT:
            prev = func
            for wraps in reversed(wrappers):
                # print(f'wrapping {wraps.__name__}')
                prev = wraps(prev)
            return prev
    
    return DecoMeta

def looseclass(orgclass):
    """
    Inject __getattr__ into a class which will correct a class if they attribute does not exist on the instance but does on the class definition
    Mainly for usage with the `pickle` module
    """

    org_getattr = getattr(orgclass, '__getattr__',  None)

    def __getattr__(self, attr):
        if org_getattr:
            # Allow classes to define their own __getattr__
            v = org_getattr(self, attr, None)
            if v:
                return v
        
        # Fill in the gap if the attribute is missing
        # Defined as a static variable on the class definition
        v = getattr(orgclass, attr, None)
        if not v:
            raise AttributeError(f'attribute "{attr}" not defined on class "{orgclass.__name__}"')
        
        # Inject default value into the class to potentially fix future usage
        setattr(self, attr, v)
        return v

    orgclass.__getattr__ = __getattr__
    return orgclass

def initializer(func):
    """
    Automatically assigns the parameters.

    >>> class process:
    ...     @initializer
    ...     def __init__(self, cmd, reachable=False, user='root'):
    ...         pass
    >>> p = process('halt', True)
    >>> p.cmd, p.reachable, p.user
    ('halt', True, 'root')
    """
    names, varargs, keywords, defaults = inspect.getargspec(func)

    @wraps(func)
    def wrapper(self, *args, **kargs):
        for name, arg in list(zip(names[1:], args)) + list(kargs.items()):
            setattr(self, name, arg)

        for name, default in zip(reversed(names), reversed(defaults)):
            if not hasattr(self, name):
                setattr(self, name, default)

        func(self, *args, **kargs)

    return wrapper