from typing import Any, Dict, List, Optional, Union, TextIO, Tuple, Literal

from .string_file_wrapper import StringFileWrapper
from .logger_config import LoggerConfig, LogLevel
from .json_context import JsonContext, ContextValues

JSONReturnType = Union[Dict[str, Any], List[Any], str, float, int, bool, None]


class JSONParser:
    def __init__(
        self,
        json_str: Union[str, StringFileWrapper],
        json_fd: Optional[TextIO],
        logging: Optional[bool],
        json_fd_chunk_length: int = 0,
    ) -> None:
        # The string to parse
        self.json_str: Union[str, StringFileWrapper] = json_str
        # Alternatively, the file description with a json file in it
        if json_fd:
            # This is a trick we do to treat the file wrapper as an array
            self.json_str = StringFileWrapper(json_fd, json_fd_chunk_length)
        # Index is our iterator that will keep track of which character we are looking at right now
        self.index: int = 0
        # This is used in the object member parsing to manage the special cases of missing quotes in key or value
        self.context = JsonContext()
        # Use this to log the activity, but only if logging is active
        self.logger = LoggerConfig(
            log_level=LogLevel.INFO if logging else LogLevel.NONE
        )

    def parse(
        self,
    ) -> Union[JSONReturnType, Tuple[JSONReturnType, List[Dict[str, str]]]]:
        json = self.parse_json()
        if self.index < len(self.json_str):
            self.log(
                "The parser returned early, checking if there's more json elements",
                LogLevel.INFO,
            )
            json = [json]
            last_index = self.index
            while self.index < len(self.json_str):
                j = self.parse_json()
                if j != "":
                    json.append(j)
                if self.index == last_index:
                    self.index += 1
                last_index = self.index
            # If nothing extra was found, don't return an array
            if len(json) == 1:
                self.log(
                    "There were no more elements, returning the element without the array",
                    LogLevel.INFO,
                )
                json = json[0]
        if self.logger.log_level == LogLevel.NONE:
            return json
        else:
            return json, self.logger.log

    def parse_json(
        self,
    ) -> JSONReturnType:
        while True:
            char = self.get_char_at()
            # False means that we are at the end of the string provided
            if char is False:
                return ""
            # <object> starts with '{'
            elif char == "{":
                self.index += 1
                return self.parse_object()
            # <array> starts with '['
            elif char == "[":
                self.index += 1
                return self.parse_array()
            # there can be an edge case in which a key is empty and at the end of an object
            # like "key": }. We return an empty string here to close the object properly
            elif char == "}":
                self.log(
                    "At the end of an object we found a key with missing value, skipping",
                    LogLevel.INFO,
                )
                return ""
            # <string> starts with a quote
            elif not self.context.is_empty() and (
                char in ['"', "'", "“"] or char.isalpha()
            ):
                return self.parse_string()
            # <number> starts with [0-9] or minus
            elif not self.context.is_empty() and (
                char.isdigit() or char == "-" or char == "."
            ):
                return self.parse_number()
            # If everything else fails, we just ignore and move on
            else:
                self.index += 1

    def parse_object(self) -> Dict[str, JSONReturnType]:
        # <object> ::= '{' [ <member> *(', ' <member>) ] '}' ; A sequence of 'members'
        obj = {}
        # Stop when you either find the closing parentheses or you have iterated over the entire string
        while (self.get_char_at() or "}") != "}":
            # This is what we expect to find:
            # <member> ::= <string> ': ' <json>

            # Skip filler whitespaces
            self.skip_whitespaces_at()

            # Sometimes LLMs do weird things, if we find a ":" so early, we'll change it to "," and move on
            if (self.get_char_at() or "") == ":":
                self.log(
                    "While parsing an object we found a : before a key, ignoring",
                    LogLevel.INFO,
                )
                self.index += 1

            # We are now searching for they string key
            # Context is used in the string parser to manage the lack of quotes
            self.context.set(ContextValues.OBJECT_KEY)

            self.skip_whitespaces_at()

            # <member> starts with a <string>
            key = ""
            while self.get_char_at():
                key = str(self.parse_string())

                if key != "" or (key == "" and self.get_char_at() == ":"):
                    # If the string is empty but there is a object divider, we are done here
                    break

            self.skip_whitespaces_at()

            # We reached the end here
            if (self.get_char_at() or "}") == "}":
                continue

            self.skip_whitespaces_at()

            # An extreme case of missing ":" after a key
            if (self.get_char_at() or "") != ":":
                self.log(
                    "While parsing an object we missed a : after a key",
                    LogLevel.INFO,
                )

            self.index += 1
            self.context.reset()
            self.context.set(ContextValues.OBJECT_VALUE)
            # The value can be any valid json
            value = self.parse_json()

            # Reset context since our job is done
            self.context.reset()
            obj[key] = value

            if (self.get_char_at() or "") in [",", "'", '"']:
                self.index += 1

            # Remove trailing spaces
            self.skip_whitespaces_at()

        self.index += 1
        return obj

    def parse_array(self) -> List[JSONReturnType]:
        # <array> ::= '[' [ <json> *(', ' <json>) ] ']' ; A sequence of JSON values separated by commas
        arr = []
        self.context.set(ContextValues.ARRAY)
        # Stop when you either find the closing parentheses or you have iterated over the entire string
        while (self.get_char_at() or "]") != "]":
            self.skip_whitespaces_at()
            value = self.parse_json()

            # It is possible that parse_json() returns nothing valid, so we stop
            if value == "":
                break

            if value == "..." and self.get_char_at(-1) == ".":
                self.log(
                    "While parsing an array, found a stray '...'; ignoring it",
                    LogLevel.INFO,
                )
            else:
                arr.append(value)

            # skip over whitespace after a value but before closing ]
            char = self.get_char_at()
            while char and (char.isspace() or char == ","):
                self.index += 1
                char = self.get_char_at()

        # Especially at the end of an LLM generated json you might miss the last "]"
        char = self.get_char_at()
        if char and char != "]":
            self.log(
                "While parsing an array we missed the closing ], adding it back",
                LogLevel.INFO,
            )
            self.index -= 1

        self.index += 1
        self.context.reset()
        return arr

    def parse_string(self) -> Union[str, bool, None]:
        # <string> is a string of valid characters enclosed in quotes
        # i.e. { name: "John" }
        # Somehow all weird cases in an invalid JSON happen to be resolved in this function, so be careful here

        # Flag to manage corner cases related to missing starting quote
        missing_quotes = False
        doubled_quotes = False
        lstring_delimiter = rstring_delimiter = '"'

        char = self.get_char_at()
        # A valid string can only start with a valid quote or, in our case, with a literal
        while char and char not in ['"', "'", "“"] and not char.isalnum():
            self.index += 1
            char = self.get_char_at()

        if not char:
            # This is an empty string
            return ""

        # Ensuring we use the right delimiter
        if char == "'":
            lstring_delimiter = rstring_delimiter = "'"
        elif char == "“":
            lstring_delimiter = "“"
            rstring_delimiter = "”"
        elif char.isalnum():
            # This could be a <boolean> and not a string. Because (T)rue or (F)alse or (N)ull are valid
            # But remember, object keys are only of type string
            if char.lower() in ["t", "f", "n"] and not self.context.is_current(
                ContextValues.OBJECT_KEY
            ):
                value = self.parse_boolean_or_null()
                if value != "":
                    return value
            self.log(
                "While parsing a string, we found a literal instead of a quote",
                LogLevel.INFO,
            )
            self.log(
                "While parsing a string, we found no starting quote. Will add the quote back",
                LogLevel.INFO,
            )
            missing_quotes = True

        if not missing_quotes:
            self.index += 1

        # There is sometimes a weird case of doubled quotes, we manage this also later in the while loop
        if self.get_char_at() == lstring_delimiter:
            # If it's an empty key, this was easy
            if (
                self.context.is_current(ContextValues.OBJECT_KEY)
                and self.get_char_at(1) == ":"
            ):
                self.index += 1
                return ""
            # Find the next delimiter
            i = self.skip_to_character(
                character=rstring_delimiter, idx=1, move_main_index=False
            )
            next_c = self.get_char_at(i)
            # Now check that the next character is also a delimiter to ensure that we have "".....""
            # In that case we ignore this rstring delimiter
            if next_c and (self.get_char_at(i + 1) or "") == rstring_delimiter:
                self.log(
                    "While parsing a string, we found a valid starting doubled quote, ignoring it",
                    LogLevel.INFO,
                )
                doubled_quotes = True
                self.index += 1
            else:
                # Ok this is not a doubled quote, check if this is an empty string or not
                i = self.skip_whitespaces_at(idx=1, move_main_index=False)
                next_c = self.get_char_at(i)
                if next_c not in [",", "]", "}"]:
                    self.log(
                        "While parsing a string, we found a doubled quote but it was a mistake, removing one quote",
                        LogLevel.INFO,
                    )
                    self.index += 1

        # Initialize our return value
        string_acc = ""

        # Here things get a bit hairy because a string missing the final quote can also be a key or a value in an object
        # In that case we need to use the ":|,|}" characters as terminators of the string
        # So this will stop if:
        # * It finds a closing quote
        # * It iterated over the entire sequence
        # * If we are fixing missing quotes in an object, when it finds the special terminators
        char = self.get_char_at()
        while char and char != rstring_delimiter:
            if (
                missing_quotes
                and self.context.is_current(ContextValues.OBJECT_KEY)
                and (char == ":" or char.isspace())
            ):
                self.log(
                    "While parsing a string missing the left delimiter in object key context, we found a :, stopping here",
                    LogLevel.INFO,
                )
                break
            if self.context.is_current(ContextValues.OBJECT_VALUE) and char in [
                ",",
                "}",
            ]:
                rstring_delimiter_missing = True
                # check if this is a case in which the closing comma is NOT missing instead
                i = self.skip_to_character(
                    character=rstring_delimiter, idx=1, move_main_index=False
                )
                next_c = self.get_char_at(i)
                if next_c:
                    i += 1
                    # found a delimiter, now we need to check that is followed strictly by a comma or brace
                    i = self.skip_whitespaces_at(idx=i, move_main_index=False)
                    next_c = self.get_char_at(i)
                    if next_c and next_c in [",", "}"]:
                        rstring_delimiter_missing = False
                if rstring_delimiter_missing:
                    self.log(
                        "While parsing a string missing the left delimiter in object value context, we found a , or } and we couldn't determine that a right delimiter was present. Stopping here",
                        LogLevel.INFO,
                    )
                    break
            string_acc += char
            self.index += 1
            char = self.get_char_at()
            if char and len(string_acc) > 0 and string_acc[-1] == "\\":
                # This is a special case, if people use real strings this might happen
                self.log("Found a stray escape sequence, normalizing it", LogLevel.INFO)
                string_acc = string_acc[:-1]
                if char in [rstring_delimiter, "t", "n", "r", "b", "\\"]:
                    escape_seqs = {"t": "\t", "n": "\n", "r": "\r", "b": "\b"}
                    string_acc += escape_seqs.get(char, char) or char
                    self.index += 1
                    char = self.get_char_at()
            # ChatGPT sometimes forget to quote stuff in html tags or markdown, so we do this whole thing here
            if char == rstring_delimiter:
                # Special case here, in case of double quotes one after another
                if doubled_quotes and self.get_char_at(1) == rstring_delimiter:
                    self.log(
                        "While parsing a string, we found a doubled quote, ignoring it",
                        LogLevel.INFO,
                    )
                    self.index += 1
                elif missing_quotes and self.context.is_current(
                    ContextValues.OBJECT_VALUE
                ):
                    # In case of missing starting quote I need to check if the delimeter is the end or the beginning of a key
                    i = 1
                    next_c = self.get_char_at(i)
                    while next_c and next_c not in [
                        rstring_delimiter,
                        lstring_delimiter,
                    ]:
                        i += 1
                        next_c = self.get_char_at(i)
                    if next_c:
                        # We found a quote, now let's make sure there's a ":" following
                        i += 1
                        # found a delimiter, now we need to check that is followed strictly by a comma or brace
                        i = self.skip_whitespaces_at(idx=i, move_main_index=False)
                        next_c = self.get_char_at(i)
                        if next_c and next_c == ":":
                            # Reset the cursor
                            self.index -= 1
                            char = self.get_char_at()
                            self.log(
                                "In a string with missing quotes and object value context, I found a delimeter but it turns out it was the beginning on the next key. Stopping here.",
                                LogLevel.INFO,
                            )
                            break
                else:
                    # Check if eventually there is a rstring delimiter, otherwise we bail
                    i = 1
                    next_c = self.get_char_at(i)
                    check_comma_in_object_value = True
                    while next_c and next_c not in [
                        rstring_delimiter,
                        lstring_delimiter,
                    ]:
                        # This is a bit of a weird workaround, essentially in object_value context we don't always break on commas
                        # This is because the routine after will make sure to correct any bad guess and this solves a corner case
                        if check_comma_in_object_value and next_c.isalpha():
                            check_comma_in_object_value = False
                        # If we are in an object context, let's check for the right delimiters
                        if (
                            (
                                self.context.is_any(ContextValues.OBJECT_KEY)
                                and next_c in [":", "}"]
                            )
                            or (
                                self.context.is_any(ContextValues.OBJECT_VALUE)
                                and next_c == "}"
                            )
                            or (
                                self.context.is_any(ContextValues.ARRAY)
                                and next_c in ["]", ","]
                            )
                            or (
                                check_comma_in_object_value
                                and self.context.is_current(ContextValues.OBJECT_VALUE)
                                and next_c == ","
                            )
                        ):
                            break
                        i += 1
                        next_c = self.get_char_at(i)
                    # If we stopped for a comma in object_value context, let's check if find a "} at the end of the string
                    if next_c == "," and self.context.is_current(
                        ContextValues.OBJECT_VALUE
                    ):
                        i += 1
                        i = self.skip_to_character(
                            character=rstring_delimiter, idx=i, move_main_index=False
                        )
                        next_c = self.get_char_at(i)
                        # Ok now I found a delimiter, let's skip whitespaces and see if next we find a }
                        i += 1
                        i = self.skip_whitespaces_at(idx=i, move_main_index=False)
                        next_c = self.get_char_at(i)
                        if next_c == "}":
                            # OK this is valid then
                            self.log(
                                "While parsing a string, we misplaced a quote that would have closed the string but has a different meaning here since this is the last element of the object, ignoring it",
                                LogLevel.INFO,
                            )
                            string_acc += str(char)
                            self.index += 1
                            char = self.get_char_at()
                    elif next_c == rstring_delimiter:
                        if self.context.is_current(ContextValues.OBJECT_VALUE):
                            # But this might not be it! This could be just a missing comma
                            # We found a delimiter and we need to check if this is a key
                            # so find a rstring_delimiter and a colon after
                            i += 1
                            i = self.skip_to_character(
                                character=rstring_delimiter,
                                idx=i,
                                move_main_index=False,
                            )
                            i += 1
                            next_c = self.get_char_at(i)
                            while next_c and next_c != ":":
                                if next_c in [
                                    lstring_delimiter,
                                    rstring_delimiter,
                                    ",",
                                ]:
                                    break
                                i += 1
                                next_c = self.get_char_at(i)
                            # Only if we fail to find a ':' then we know this is misplaced quote
                            if next_c != ":":
                                self.log(
                                    "While parsing a string, we a misplaced quote that would have closed the string but has a different meaning here, ignoring it",
                                    LogLevel.INFO,
                                )
                                string_acc += str(char)
                                self.index += 1
                                char = self.get_char_at()

        if (
            char
            and missing_quotes
            and self.context.is_current(ContextValues.OBJECT_KEY)
            and char.isspace()
        ):
            self.log(
                "While parsing a string, handling an extreme corner case in which the LLM added a comment instead of valid string, invalidate the string and return an empty value",
                LogLevel.INFO,
            )
            self.skip_whitespaces_at()
            if self.get_char_at() not in [":", ","]:
                return ""

        # A fallout of the previous special case in the while loop,
        # we need to update the index only if we had a closing quote
        if char != rstring_delimiter:
            self.log(
                "While parsing a string, we missed the closing quote, ignoring",
                LogLevel.INFO,
            )
        else:
            self.index += 1

        return string_acc.rstrip()

    def parse_number(self) -> Union[float, int, str, JSONReturnType]:
        # <number> is a valid real number expressed in one of a number of given formats
        number_str = ""
        number_chars = set("0123456789-.eE/,")
        char = self.get_char_at()
        is_array = self.context.is_current(ContextValues.ARRAY)
        while char and char in number_chars and (char != "," or not is_array):
            number_str += char
            self.index += 1
            char = self.get_char_at()
        if len(number_str) > 1 and number_str[-1] in "-eE/,":
            # The number ends with a non valid character for a number/currency, rolling back one
            number_str = number_str[:-1]
            self.index -= 1
        try:
            if "," in number_str:
                return str(number_str)
            if "." in number_str or "e" in number_str or "E" in number_str:
                return float(number_str)
            elif number_str == "-":
                # If there is a stray "-" this will throw an exception, throw away this character
                return self.parse_json()
            else:
                return int(number_str)
        except ValueError:
            return number_str

    def parse_boolean_or_null(self) -> Union[bool, str, None]:
        # <boolean> is one of the literal strings 'true', 'false', or 'null' (unquoted)
        starting_index = self.index
        char = (self.get_char_at() or "").lower()
        value: Optional[Tuple[str, Optional[bool]]]
        if char == "t":
            value = ("true", True)
        elif char == "f":
            value = ("false", False)
        elif char == "n":
            value = ("null", None)

        if value:
            i = 0
            while char and i < len(value[0]) and char == value[0][i]:
                i += 1
                self.index += 1
                char = (self.get_char_at() or "").lower()
            if i == len(value[0]):
                return value[1]

        # If nothing works reset the index before returning
        self.index = starting_index
        return ""

    def get_char_at(self, count: int = 0) -> Union[str, Literal[False]]:
        # Why not use something simpler? Because try/except in python is a faster alternative to an "if" statement that is often True
        try:
            return self.json_str[self.index + count]
        except IndexError:
            return False

    def skip_whitespaces_at(self, idx: int = 0, move_main_index=True) -> int:
        """
        This function quickly iterates on whitespaces, syntactic sugar to make the code more concise
        """
        try:
            char = self.json_str[self.index + idx]
        except IndexError:
            return idx
        while char.isspace():
            if move_main_index:
                self.index += 1
            else:
                idx += 1
            try:
                char = self.json_str[self.index + idx]
            except IndexError:
                return idx
        return idx

    def skip_to_character(
        self, character: str, idx: int = 0, move_main_index=True
    ) -> int:
        """
        This function quickly iterates to find a character, syntactic sugar to make the code more concise
        """
        try:
            char = self.json_str[self.index + idx]
        except IndexError:
            return idx
        while char != character:
            if move_main_index:  # pragma: no cover
                self.index += 1
            else:
                idx += 1
            try:
                char = self.json_str[self.index + idx]
            except IndexError:
                return idx
        return idx

    def log(self, text: str, level: LogLevel) -> None:
        if level == self.logger.log_level:
            context = ""
            start = max(self.index - self.logger.window, 0)
            end = min(self.index + self.logger.window, len(self.json_str))
            context = self.json_str[start:end]
            self.logger.log.append(
                {
                    "text": text,
                    "context": context,
                }
            )
