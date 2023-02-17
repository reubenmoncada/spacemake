import argparse
import pandas as pd

def setup_parser(parser):
    parser.add_argument(
        "--output",
        type=str,
        help="where to store the output file with puck names and global (x,y,z) coordinates",
        required=True,
    )

    parser.add_argument(
        "--format-string",
        type=str,
        help="this the format for puck names. There are 4 attributes that can be chosen:"
        + "\{lane\} (int), \{column\} (int), \{row\} (int), \{side_letter\} (str), \{side_number\} (int).\n"
        + "For instance, a valid string format would be: \n"
        + "fc_009_L{lane}{side_letter}_tile_{side_number}{column}{row:02d}\n"
        + "This name must be used, as is, when creating a new sample in spacemake.",
        default="L{lane}{side_letter}_tile_{side_number}{column}{row:02d}",
    )

    parser.add_argument(
        "--x-offset",
        type=int,
        help="the offset in the x axis. Units are important during puck collection generation.",
        default=33809,
    )

    parser.add_argument(
        "--y-offset",
        type=int,
        help="the offset of the y axis. Units are important during puck collection generation.",
        default=36342,
    )

    parser.add_argument(
        "--swath-offset-odd",
        type=int,
        help="the swath offset for odd columns",
        default=0,
    )

    parser.add_argument(
        "--swath-offset-even",
        type=int,
        help="the swath offset for even columns",
        default=6201,
    )

    parser.add_argument(
        "--rows",
        type=int,
        help="number of rows",
        default=78,
    )

    parser.add_argument(
        "--columns",
        type=int,
        help="number of columns",
        default=6,
    )

    parser.add_argument(
        "--n_lanes",
        type=int,
        help="number of lanes",
        default=4,
    )

    parser.add_argument(
        "--zero-coded",
        default=False,
        action="store_true",
        help="whether row and column indices should start at 0, instead of 1",
    )

    return parser


def create_coordinate_system(
    n_lanes,
    n_cols,
    n_rows,
    x_offset,
    y_offset,
    swath_offsets_odd,
    swath_offsets_even,
    zero_coded,
    format_string,
):
    one_coded_offset = 0 if zero_coded else 1
    swath_offsets = [swath_offsets_even, swath_offsets_odd]
    sides_letter = {1: "a", 2: "b"}
    l = []
    for lane in range(one_coded_offset, n_lanes + one_coded_offset):
        for side in [1, 2]:
            for col in range(n_cols + one_coded_offset):
                for row in range(one_coded_offset, n_rows + one_coded_offset):
                    puck_id = format_string.format(
                        lane=lane,
                        side_letter=sides_letter[side],
                        side_number=side,
                        column=col,
                        row=row,
                    )

                    x_ofs = int(col) * x_offset

                    y_ofs = int(row) * y_offset + swath_offsets[int(col) % 2]

                    z_ofs = 0

                    l.append(
                        pd.DataFrame(
                            {
                                "puck_id": [puck_id],
                                "x_ofset": [x_ofs],
                                "y_offset": [y_ofs],
                                "z_offset": [z_ofs],
                            }
                        )
                    )

    puck_names_coords = pd.concat(l)

    return puck_names_coords


def cmdline():
    """cmdline."""
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="create a global coordinate system for a novaseq S4 flow cell",
    )
    parser = setup_parser(parser)
    args = parser.parse_args()

    puck_names_coords = create_coordinate_system(
        n_lanes=args.n_lanes,
        n_cols=args.columns,
        n_rows=args.rows,
        x_offset=args.x_offset,
        y_offset=args.y_offset,
        swath_offsets_odd=args.swath_offset_odd,
        swath_offsets_even=args.swath_offset_even,
        zero_coded=args.zero_coded,
        format_string=args.format_string,
    )

    puck_names_coords.to_csv(args.output, index=False)


if __name__ == "__main__":
    cmdline()
