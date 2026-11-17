import {HostProvider} from './host/Host';
import WorkbenchMount from './plugins/WorkbenchMount';
import HostRecovery from './host/Recovery';
import AppNavigation from './AppNavigation';
export default function App() {
  return <div className="window-shell"><HostProvider><AppNavigation/><div className="window-content"><WorkbenchMount/><HostRecovery/></div></HostProvider></div>;
}
