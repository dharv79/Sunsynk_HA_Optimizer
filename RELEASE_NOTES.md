# Sunsynk Optimizer 2.0.1

## Bill check follows your bill's own dates

Octopus bills don't run by calendar month: for example, usage from the 8th to the 7th.

**New option:** "Bill usage period start day", under Settings → Devices & services → Sunsynk Optimizer → Configure, on the first page below the gas cost sensor.
- Set it to the first date in your bill's "We have charged you" lines.
- The default of 1 keeps the calendar-month check.

**Entering a bill amount:** on the Energy & cost tab, type the amount into the Bill check field. It's checked against the latest complete bill period before today.
- Use the bill's "Total charges for bill" if your gas cost sensor is set. That figure is charges minus export credit.
- If it isn't set, use electricity charges minus export credit.

The Bill check card now reads "Latest bill, net of export (£)" and shows the bill period.

