within;
package Model "The model this repository builds: rename it, and edit it in OMEdit or as text"
  model Tank "A tank's water following its inflow: der(water) = (inflow - water) / tau_s"
    parameter Real tau_s(unit = "s", min = 0) = 600 "Time constant";
    parameter Real water_start(unit = "degC") = 20 "Water temperature at the start";
    input Real inflow(unit = "degC") "Inflow temperature";
    output Real water(unit = "degC") "Water temperature";
  initial equation
    water = water_start;
  equation
    der(water) = (inflow - water) / tau_s;
  end Tank;
end Model;
