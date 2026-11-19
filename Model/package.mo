within;
package Model "The model this repository builds: rename it, and edit it in OMEdit or as text"
  model Tank "A tank's water following its inflow: der(water) = (inflow - water) / tau_s"
    import Modelica.Units.SI;
    import Modelica.Units.NonSI;
    parameter SI.Time tau_s(min = 0) = 600 "Time constant";
    parameter NonSI.Temperature_degC water_start = 20 "Water temperature at the start";
    input NonSI.Temperature_degC inflow "Inflow temperature";
    output NonSI.Temperature_degC water "Water temperature";
  initial equation
    water = water_start;
  equation
    der(water) = (inflow - water) / tau_s;
  end Tank;
  annotation(uses(Modelica(version = "4.1.0")));
end Model;
