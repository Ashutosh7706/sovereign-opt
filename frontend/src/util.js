export function html(strings, ...values) {
  return strings.reduce((result, string, index) => {
    const value = values[index];

    if (value === undefined) {
      return result + string;
    }

    return result + String(value);
  }, "");
}