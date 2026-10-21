// The colours tags and notes folders can be given (the menus show them by name).
export const PALETTE = ['#e5484d', '#f76b15', '#ffc53d', '#30a46c', '#12a594', '#0090ff', '#6e56cf', '#d6409f', '#8d8d86'];

const NAMES: Record<string, string> = {
  '#e5484d': 'Red', '#f76b15': 'Orange', '#ffc53d': 'Yellow', '#30a46c': 'Green', '#12a594': 'Teal',
  '#0090ff': 'Blue', '#6e56cf': 'Purple', '#d6409f': 'Pink', '#8d8d86': 'Grey',
};

/** A colour's name for a menu ("Blue"); menus show the colour itself as a swatch. */
export const colorName = (c: string): string => NAMES[c] || c;
